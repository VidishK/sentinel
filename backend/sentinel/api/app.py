from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sentinel.agent.loop import run_turn
from sentinel.auth import (
    login as auth_login,
    login_google,
    logout as auth_logout,
    public_config,
    session_user,
    signup as auth_signup,
    verify_email,
)
from sentinel.config import get_settings
from sentinel.connectors.catalog import (
    add_connection,
    delete_connection,
    list_connections,
    refresh_active_catalog,
    set_active,
)
from sentinel.db import connect
from sentinel.memory.rules import (
    create_rule,
    get_rule,
    list_rules,
    reembed_rule,
    search_rules,
    set_rule_status,
    similar_rules,
    update_rule,
)
from sentinel.memory.actions import search_similar_actions
from sentinel.memory.schema_memory import reindex_schema_docs, search_schema_docs
from sentinel.memory.trials import evaluate_and_decide
from sentinel.safety.backup import (
    capture_restore_point,
    guard_high_risk_commit,
    list_cloud_backups,
)
from sentinel.safety.policy import (
    PolicyViolation,
    check_typed_confirm,
    enforce_write_proposal,
    get_policy,
    needs_typed_confirm,
    update_policy,
)
from sentinel.safety.preview import PreviewResult, approve_and_commit, preview_sql
from sentinel.tenancy import TenantMiddleware, require_user_id


class PreviewRequest(BaseModel):
    sql: str = Field(min_length=1)
    session_id: UUID | None = None
    request: str = "manual preview"


class ApproveRequest(BaseModel):
    action_id: UUID
    confirmation: str | None = Field(
        default=None,
        description="Must be COMMIT for medium/high risk when policy requires it",
    )


class RejectRequest(BaseModel):
    action_id: UUID
    reason: str | None = None


class PolicyUpdateRequest(BaseModel):
    mode: str | None = None
    allow_writes: bool | None = None
    block_ddl: bool | None = None
    require_where: bool | None = None
    max_preview_rows: int | None = None
    require_typed_confirm_for: list[str] | None = None
    auto_apply_low_risk: bool | None = None
    auto_apply_max_rows: int | None = None


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        examples=["How many real non-test customers do we have?"],
    )
    # Omit on the first message. Reuse the session_id from the response after that.
    session_id: UUID | None = Field(default=None, examples=[None])


class CreateRuleRequest(BaseModel):
    body: str = Field(min_length=1, examples=["Always exclude customers where is_test = true."])
    trigger_text: str = Field(
        min_length=1,
        examples=["counting customers or reporting user metrics"],
    )


class EvaluateRuleRequest(BaseModel):
    task_family: str | None = Field(
        default=None,
        examples=["test_accounts"],
        description="Optional family filter to keep evals fast during demos",
    )


class UpdateRuleRequest(BaseModel):
    body: str | None = None
    trigger_text: str | None = None


class RuleStatusRequest(BaseModel):
    status: str = Field(examples=["retired", "rejected", "probation"])
    reason: str | None = None


class HealthResponse(BaseModel):
    status: str
    database: str


class CreateConnectionRequest(BaseModel):
    name: str = Field(min_length=1)
    engine: str = Field(examples=["postgres", "mongodb", "cockroach"])
    dsn: str = Field(min_length=1, description="postgres://... or mongodb://...")


class SignupRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    name: str | None = None


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=10)


class VerifyRequest(BaseModel):
    token: str = Field(min_length=8)


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
    app.add_middleware(TenantMiddleware)
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

    @app.get("/auth/config")
    def auth_config() -> dict[str, Any]:
        return public_config()

    @app.post("/auth/signup")
    def auth_signup_route(body: SignupRequest) -> dict[str, Any]:
        try:
            return auth_signup(body.email, body.password, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/auth/login")
    def auth_login_route(body: LoginRequest) -> dict[str, Any]:
        try:
            return auth_login(body.email, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/auth/google")
    def auth_google_route(body: GoogleLoginRequest) -> dict[str, Any]:
        try:
            return login_google(body.id_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/auth/verify")
    def auth_verify_route(body: VerifyRequest) -> dict[str, Any]:
        try:
            return verify_email(body.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/auth/me")
    def auth_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        user = session_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return {"user": user}

    @app.post("/auth/logout")
    def auth_logout_route(authorization: str | None = Header(default=None)) -> dict[str, str]:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        auth_logout(token)
        return {"status": "ok"}

    @app.get("/policy")
    def read_policy() -> dict[str, Any]:
        return get_policy().to_dict()

    @app.put("/policy")
    def write_policy(body: PolicyUpdateRequest) -> dict[str, Any]:
        patch = body.model_dump(exclude_none=True)
        return update_policy(patch).to_dict()

    @app.get("/backups")
    def backups() -> dict[str, Any]:
        try:
            rows = list_cloud_backups()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "cluster": get_settings().ccloud_cluster_name,
            "backups": rows,
        }

    @app.get("/connections")
    def connections() -> dict[str, Any]:
        return {"connections": list_connections()}

    @app.post("/connections")
    def create_connection(body: CreateConnectionRequest) -> dict[str, Any]:
        try:
            return add_connection(body.name, body.engine, body.dsn)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/connections/{connection_id}/activate")
    def activate_connection(connection_id: UUID) -> dict[str, Any]:
        try:
            return set_active(str(connection_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/connections/refresh")
    def refresh_connection() -> dict[str, Any]:
        try:
            return refresh_active_catalog()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.delete("/connections/{connection_id}")
    def drop_connection(connection_id: UUID) -> dict[str, Any]:
        try:
            delete_connection(str(connection_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "deleted"}

    @app.post("/actions/preview")
    def create_preview(body: PreviewRequest) -> dict[str, Any]:
        try:
            enforce_write_proposal(body.sql)
        except PolicyViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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
                    """
                    INSERT INTO sentinel.sessions (id, title, user_id)
                    VALUES (%s, %s, %s)
                    """,
                    (str(session_id), "api session", require_user_id()),
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
                SELECT a.id, a.sql_text, a.status, a.risk, a.risk_reasons, s.user_id
                FROM sentinel.actions a
                LEFT JOIN sentinel.sessions s ON s.id = a.session_id
                WHERE a.id = %s
                """,
                (str(body.action_id),),
            )
            action = cur.fetchone()
            if action is None:
                raise HTTPException(status_code=404, detail="action not found")
            if action.get("user_id") and str(action["user_id"]) != require_user_id():
                raise HTTPException(status_code=404, detail="action not found")
            if action["status"] != "previewed":
                raise HTTPException(
                    status_code=400,
                    detail=f"action is {action['status']}, expected previewed",
                )

            try:
                enforce_write_proposal(action["sql_text"])
                check_typed_confirm(action["risk"], body.confirmation)
            except PolicyViolation as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc

            restore_point = guard_high_risk_commit(action["risk"])
            if restore_point:
                cur.execute(
                    """
                    UPDATE sentinel.actions
                    SET backup_id = %s
                    WHERE id = %s
                    """,
                    (restore_point["backup_id"], str(body.action_id)),
                )
                cur.execute(
                    """
                    INSERT INTO sentinel.audit_events
                        (action_id, event_type, actor, payload)
                    VALUES (%s, 'backup_recorded', 'ccloud', %s::jsonb)
                    """,
                    (str(body.action_id), json.dumps(restore_point, default=str)),
                )

            try:
                if str(action["sql_text"]).lstrip().startswith("{"):
                    from sentinel.connectors.catalog import apply_mongo_write

                    result = apply_mongo_write(action["sql_text"])
                else:
                    result = approve_and_commit(action["sql_text"])
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
                    json.dumps(
                        {
                            **result,
                            "confirmation_required": needs_typed_confirm(action["risk"]),
                            "restore_point": restore_point,
                        },
                        default=str,
                    ),
                ),
            )

        payload = {"action_id": str(body.action_id), **result}
        if restore_point:
            payload["restore_point"] = restore_point
        return payload

    @app.post("/actions/reject")
    def reject(body: RejectRequest) -> dict[str, Any]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.status, s.user_id
                FROM sentinel.actions a
                LEFT JOIN sentinel.sessions s ON s.id = a.session_id
                WHERE a.id = %s
                """,
                (str(body.action_id),),
            )
            action = cur.fetchone()
            if action is None:
                raise HTTPException(status_code=404, detail="action not found")
            if action.get("user_id") and str(action["user_id"]) != require_user_id():
                raise HTTPException(status_code=404, detail="action not found")
            if action["status"] != "previewed":
                raise HTTPException(
                    status_code=400,
                    detail=f"action is {action['status']}, expected previewed",
                )
            cur.execute(
                """
                UPDATE sentinel.actions
                SET status = 'rejected'
                WHERE id = %s
                """,
                (str(body.action_id),),
            )
            cur.execute(
                """
                INSERT INTO sentinel.audit_events
                    (action_id, event_type, actor, payload)
                VALUES (%s, 'rejected', 'human', %s::jsonb)
                """,
                (
                    str(body.action_id),
                    json.dumps({"reason": body.reason or "rejected in UI"}),
                ),
            )
        return {"action_id": str(body.action_id), "status": "rejected"}

    @app.get("/actions/{action_id}")
    def get_action(action_id: UUID) -> dict[str, Any]:
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.*, s.user_id AS owner_id
                FROM sentinel.actions a
                LEFT JOIN sentinel.sessions s ON s.id = a.session_id
                WHERE a.id = %s
                """,
                (str(action_id),),
            )
            action = cur.fetchone()
            if action is None:
                raise HTTPException(status_code=404, detail="action not found")
            if action.get("owner_id") and str(action["owner_id"]) != require_user_id():
                raise HTTPException(status_code=404, detail="action not found")
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

    @app.post("/chat")
    def chat(body: ChatRequest) -> dict[str, Any]:
        try:
            result = run_turn(
                body.message,
                session_id=str(body.session_id) if body.session_id else None,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result

    @app.get("/sessions/{session_id}/messages")
    def session_messages(session_id: UUID) -> dict[str, Any]:
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM sentinel.sessions WHERE id = %s",
                (str(session_id),),
            )
            session = cur.fetchone()
            if session and session["user_id"] and str(session["user_id"]) != require_user_id():
                raise HTTPException(status_code=404, detail="session not found")
            cur.execute(
                """
                SELECT id, role, content, created_at
                FROM sentinel.messages
                WHERE session_id = %s
                ORDER BY created_at
                """,
                (str(session_id),),
            )
            messages = list(cur.fetchall())
            cur.execute(
                """
                SELECT id, sql_text, kind, risk, rows_affected, status,
                       diff_summary, backup_id, created_at, committed_at
                FROM sentinel.actions
                WHERE session_id = %s
                ORDER BY created_at
                """,
                (str(session_id),),
            )
            actions = list(cur.fetchall())
        return {"session_id": str(session_id), "messages": messages, "actions": actions}

    @app.get("/rules")
    def rules(status: str | None = None) -> dict[str, Any]:
        return {"rules": list_rules(status)}

    @app.get("/rules/search")
    def rules_search(q: str, status: str | None = None, limit: int = 10) -> dict[str, Any]:
        if not q.strip():
            raise HTTPException(status_code=400, detail="q is required")
        try:
            return {
                "query": q,
                "rules": search_rules(q, limit=limit, status=status),
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/memory/retrieve")
    def memory_retrieve(q: str, limit: int = 5) -> dict[str, Any]:
        """Unified semantic retrieval over rules, actions, and schema docs."""
        if not q.strip():
            raise HTTPException(status_code=400, detail="q is required")
        try:
            return {
                "query": q,
                "rules": search_rules(q, limit=limit),
                "actions": search_similar_actions(q, limit=limit),
                "schema": search_schema_docs(q, limit=min(limit, 4)),
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/memory/reindex-schema")
    def memory_reindex_schema() -> dict[str, Any]:
        try:
            return reindex_schema_docs()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/rules/{rule_id}")
    def rule_detail(rule_id: UUID) -> dict[str, Any]:
        rule = get_rule(str(rule_id))
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        return {
            **rule,
            "similar": similar_rules(str(rule_id)),
        }

    @app.post("/rules")
    def add_rule(body: CreateRuleRequest) -> dict[str, Any]:
        try:
            return create_rule(body.body, body.trigger_text, status="probation")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.patch("/rules/{rule_id}")
    def patch_rule(rule_id: UUID, body: UpdateRuleRequest) -> dict[str, Any]:
        try:
            return update_rule(
                str(rule_id),
                body=body.body,
                trigger_text=body.trigger_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/rules/{rule_id}/status")
    def change_rule_status(rule_id: UUID, body: RuleStatusRequest) -> dict[str, Any]:
        try:
            return set_rule_status(str(rule_id), body.status, reason=body.reason)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/rules/{rule_id}/reembed")
    def reembed(rule_id: UUID) -> dict[str, Any]:
        try:
            return reembed_rule(str(rule_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/rules/{rule_id}/similar")
    def rule_similar(rule_id: UUID, limit: int = 5) -> dict[str, Any]:
        return {"rules": similar_rules(str(rule_id), limit=limit)}

    @app.post("/rules/{rule_id}/evaluate")
    def evaluate_rule(rule_id: UUID, body: EvaluateRuleRequest | None = None) -> dict[str, Any]:
        family = body.task_family if body else None
        try:
            return evaluate_and_decide(str(rule_id), task_family=family)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


app = create_app()
