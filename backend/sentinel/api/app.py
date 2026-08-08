from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sentinel.db import connect
from sentinel.safety.preview import PreviewResult, approve_and_commit, preview_sql


class PreviewRequest(BaseModel):
    sql: str = Field(min_length=1)
    session_id: UUID | None = None
    request: str = "manual preview"


class ApproveRequest(BaseModel):
    action_id: UUID


class HealthResponse(BaseModel):
    status: str
    database: str


def _preview_payload(preview: PreviewResult) -> dict[str, Any]:
    return {
        "sql": preview.sql_text,
        "kind": preview.kind,
        "risk": {
            "level": preview.risk.level,
            "reasons": preview.risk.reasons,
            "requires_backup": preview.risk.requires_backup,
        },
        "rows_affected": preview.rows_affected,
        "before": preview.before,
        "after": preview.after,
        "result_rows": preview.result_rows,
        "summary": preview.summary,
        "error": preview.error,
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sentinel",
        description="An AI agent that proves itself before it touches your database.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            db = cur.fetchone()["current_database"]
        return HealthResponse(status="ok", database=db)

    @app.post("/actions/preview")
    def create_preview(body: PreviewRequest) -> dict[str, Any]:
        preview = preview_sql(body.sql)
        if preview.error and preview.kind != "read":
            # Still persist the failed attempt for the audit trail.
            pass

        action_id = uuid4()
        session_id = body.session_id
        with connect() as conn, conn.cursor() as cur:
            if session_id is None:
                session_id = uuid4()
                cur.execute(
                    "INSERT INTO sentinel.sessions (id, title) VALUES (%s, %s)",
                    (str(session_id), "api session"),
                )
            cur.execute(
                """
                INSERT INTO sentinel.actions (
                    id, session_id, request, sql_text, kind, risk,
                    risk_reasons, rows_affected, diff_summary, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s::jsonb, %s
                )
                """,
                (
                    str(action_id),
                    str(session_id),
                    body.request,
                    preview.sql_text,
                    preview.kind,
                    preview.risk.level,
                    json.dumps(preview.risk.reasons),
                    preview.rows_affected,
                    json.dumps(preview.summary, default=str),
                    "previewed" if preview.error is None else "failed",
                ),
            )
            cur.execute(
                """
                INSERT INTO sentinel.audit_events (action_id, event_type, actor, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (
                    str(action_id),
                    "previewed" if preview.error is None else "preview_failed",
                    "api",
                    json.dumps(_preview_payload(preview), default=str),
                ),
            )

        return {
            "action_id": str(action_id),
            "session_id": str(session_id),
            "preview": _preview_payload(preview),
        }

    @app.post("/actions/approve")
    def approve(body: ApproveRequest) -> dict[str, Any]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, sql_text, status, risk, risk_reasons
                FROM sentinel.actions WHERE id = %s
                """,
                (str(body.action_id),),
            )
            action = cur.fetchone()
            if action is None:
                raise HTTPException(status_code=404, detail="action not found")
            if action["status"] != "previewed":
                raise HTTPException(
                    status_code=400,
                    detail=f"action is {action['status']}, expected previewed",
                )

            try:
                result = approve_and_commit(action["sql_text"], conn=conn)
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                with connect() as err_conn, err_conn.cursor() as err_cur:
                    err_cur.execute(
                        """
                        UPDATE sentinel.actions
                        SET status = 'failed'
                        WHERE id = %s
                        """,
                        (str(body.action_id),),
                    )
                    err_cur.execute(
                        """
                        INSERT INTO sentinel.audit_events
                            (action_id, event_type, actor, payload)
                        VALUES (%s, 'commit_failed', 'api', %s::jsonb)
                        """,
                        (
                            str(body.action_id),
                            json.dumps({"error": str(exc)}),
                        ),
                    )
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            cur.execute(
                """
                UPDATE sentinel.actions
                SET status = 'committed', committed_at = now()
                WHERE id = %s
                """,
                (str(body.action_id),),
            )
            cur.execute(
                """
                INSERT INTO sentinel.audit_events
                    (action_id, event_type, actor, payload)
                VALUES (%s, 'committed', 'api', %s::jsonb)
                """,
                (
                    str(body.action_id),
                    json.dumps(result, default=str),
                ),
            )

        return {"action_id": str(body.action_id), **result}

    @app.get("/actions/{action_id}")
    def get_action(action_id: UUID) -> dict[str, Any]:
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM sentinel.actions WHERE id = %s",
                (str(action_id),),
            )
            action = cur.fetchone()
            if action is None:
                raise HTTPException(status_code=404, detail="action not found")
            cur.execute(
                """
                SELECT event_type, actor, payload, at
                FROM sentinel.audit_events
                WHERE action_id = %s
                ORDER BY at
                """,
                (str(action_id),),
            )
            events = list(cur.fetchall())
        return {"action": action, "events": events}

    return app


app = create_app()
