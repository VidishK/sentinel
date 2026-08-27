"""Per-account isolation for sources, sessions, and memory."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

current_user_id: ContextVar[str | None] = ContextVar("sentinel_user_id", default=None)

_PUBLIC = {
    "/health",
    "/auth/config",
    "/auth/signup",
    "/auth/login",
    "/auth/google",
    "/auth/verify",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def user_id() -> str | None:
    return current_user_id.get()


def require_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        raise RuntimeError("Sign in to continue.")
    return uid


def bind_user(uid: str) -> Token[str | None]:
    return current_user_id.set(uid)


def ensure_tenant_columns() -> None:
    from sentinel.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE sentinel.connections ADD COLUMN IF NOT EXISTS user_id UUID"
        )
        cur.execute(
            "ALTER TABLE sentinel.sessions ADD COLUMN IF NOT EXISTS user_id UUID"
        )
        cur.execute("ALTER TABLE sentinel.rules ADD COLUMN IF NOT EXISTS user_id UUID")
        cur.execute(
            "ALTER TABLE sentinel.schema_docs ADD COLUMN IF NOT EXISTS user_id UUID"
        )


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path.startswith("/api/"):
            path = path[4:]
        if path in _PUBLIC or path.startswith("/docs"):
            return await call_next(request)

        from sentinel.auth import session_user

        header = request.headers.get("authorization") or ""
        token = None
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()
        user = session_user(token)
        if user is None:
            return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
        bound = current_user_id.set(str(user["id"]))
        try:
            return await call_next(request)
        finally:
            current_user_id.reset(bound)
