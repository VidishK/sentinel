"""Run with:  cd backend && uvicorn sentinel.main:app --reload --port 8001"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sentinel.api.app import app as api_app


def _frontend_dist() -> Path | None:
    # Local `uvicorn --reload` must stay API-only so Vite can proxy /api.
    # Docker/Cloud Run set FRONTEND_DIST (or SERVE_FRONTEND=1).
    raw = os.environ.get("FRONTEND_DIST")
    serve = os.environ.get("SERVE_FRONTEND", "").lower() in {"1", "true", "yes"}
    if not raw and not serve:
        return None
    candidates = [Path(raw)] if raw else []
    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parents[2] / "frontend" / "dist",
            here.parent.parent / "frontend" / "dist",
            Path("/app/frontend/dist"),
        ]
    )
    for path in candidates:
        if path and (path / "index.html").is_file():
            return path
    return None


def _with_frontend(dist: Path) -> FastAPI:
    app = FastAPI(title="Sentinel")
    app.mount("/api", api_app)
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            return FileResponse(dist / "index.html")
        target = dist / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(dist / "index.html")

    return app


_dist = _frontend_dist()
app = _with_frontend(_dist) if _dist else api_app

__all__ = ["app"]
