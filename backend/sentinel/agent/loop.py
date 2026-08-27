"""Chat agent loop backed by Bedrock Converse + tool use."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import boto3

from sentinel.agent.tools import TOOL_SPECS, dispatch
from sentinel.config import get_settings
from sentinel.db import connect
from sentinel.memory.rules import format_rules_for_prompt, search_trusted_rules
from sentinel.memory.actions import format_actions_for_prompt, search_similar_actions
from sentinel.memory.schema_memory import format_schema_for_prompt, search_schema_docs
from sentinel.connectors.catalog import format_source_for_prompt
from sentinel.safety.policy import get_policy
from sentinel.tenancy import ensure_tenant_columns, require_user_id

SYSTEM_PROMPT = """\
You are Sentinel, an AI database agent. You operate on the ACTIVE data source \
(Cockroach, Postgres, or MongoDB). Memory (rules, audit, embeddings) always \
lives in CockroachDB.

Hard rules:
1. Call list_tables first if you are not sure what exists. Only query objects \
listed on the active source. Qualify SQL with the schema from list_tables \
(for example public.products). Do not assume company.* unless it is listed.
2. For a new change request, ALWAYS call propose_write (SQL) or \
mongo_propose_write (Mongo). Never reuse an old action_id. Never describe how \
to approve a previous write instead of proposing this one.
3. If the tool returns status auto_committed: one or two sentences — what \
changed, that it is applied, that Audit has it. Do not ask for approval.
4. If the tool returns status awaiting_approval: one sentence — it is waiting \
in Preview on the right. Do NOT mention action IDs. Do NOT give numbered \
steps. Do NOT say "Open Live memory" or "click Approve".
5. Reads: run_readonly_sql (SQL) or mongo_find (Mongo).
6. Never touch sentinel.* or system catalogs.
7. When the user states a durable convention, call propose_rule (probation).
8. Obey trusted rules below. Be concise. Show numbers. No tutorials.
9. Policy may be read-only, block DDL, and require WHERE. Do not retry blocked SQL.
"""


def _runtime():
    settings = get_settings()
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def _ensure_session(session_id: str | None, title: str) -> str:
    """Return a valid session id, creating the row if needed.

    Swagger / clients often send a placeholder UUID. If that id is not in
    `sentinel.sessions` yet, create it instead of failing the FK on messages.
    """
    new_id = session_id or str(uuid4())
    uid = require_user_id()
    ensure_tenant_columns()
    with connect() as conn, conn.cursor() as cur:
        if session_id:
            cur.execute(
                "SELECT user_id FROM sentinel.sessions WHERE id = %s",
                (session_id,),
            )
            existing = cur.fetchone()
            if existing and existing["user_id"] and str(existing["user_id"]) != uid:
                new_id = str(uuid4())
        cur.execute(
            """
            INSERT INTO sentinel.sessions (id, title, user_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (new_id, title[:80], uid),
        )
        cur.execute(
            """
            UPDATE sentinel.sessions
            SET user_id = COALESCE(user_id, %s)
            WHERE id = %s
            """,
            (uid, new_id),
        )
    return new_id


def _persist_message(session_id: str, role: str, content: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sentinel.messages (session_id, role, content)
            VALUES (%s, %s, %s)
            """,
            (session_id, role, content),
        )


def _load_history(session_id: str, limit: int = 16) -> list[dict[str, Any]]:
    """Load prior turns as Converse messages (text-only; tool traces are not replayed)."""
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT role, content
            FROM sentinel.messages
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        rows = list(reversed(cur.fetchall()))
    messages: list[dict[str, Any]] = []
    for row in rows:
        role = "user" if row["role"] == "user" else "assistant"
        messages.append({"role": role, "content": [{"text": row["content"]}]})
    return messages


def run_turn(
    user_text: str,
    *,
    session_id: str | None = None,
    max_tool_rounds: int = 8,
) -> dict[str, Any]:
    """Run one user turn. Returns assistant text, session id, and any previews."""
    settings = get_settings()
    sid = _ensure_session(session_id, user_text)
    _persist_message(sid, "user", user_text)

    trusted = search_trusted_rules(user_text, limit=5)
    past_actions = search_similar_actions(user_text, limit=4)
    schema_hits = search_schema_docs(user_text, limit=4)
    policy = get_policy()
    auto_line = (
        f"Auto-apply low-risk writes is ON (max {policy.auto_apply_max_rows} rows)."
        if policy.auto_apply_low_risk
        else "Auto-apply is OFF; every write waits for Preview approval."
    )
    system = (
        SYSTEM_PROMPT
        + "\n\n"
        + auto_line
        + "\n\n"
        + format_source_for_prompt()
        + "\n\n"
        + format_rules_for_prompt(trusted)
        + "\n\n"
        + format_schema_for_prompt(schema_hits)
        + "\n\n"
        + format_actions_for_prompt(past_actions)
    )

    messages = _load_history(sid)
    pending_previews: list[dict[str, Any]] = []
    client = _runtime()

    for _ in range(max_tool_rounds):
        response = client.converse(
            modelId=settings.bedrock_model_id,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": TOOL_SPECS},
            inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
        )

        output_message = response["output"]["message"]
        messages.append(output_message)
        stop = response.get("stopReason")

        tool_uses = [
            block["toolUse"]
            for block in output_message.get("content", [])
            if "toolUse" in block
        ]
        text_parts = [
            block["text"]
            for block in output_message.get("content", [])
            if "text" in block
        ]

        if stop == "tool_use" and tool_uses:
            result_blocks: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                name = tool_use["name"]
                tool_use_id = tool_use["toolUseId"]
                args = tool_use.get("input") or {}
                try:
                    result = dispatch(
                        name,
                        args,
                        session_id=sid,
                        user_request=user_text,
                    )
                    if name in {"propose_write", "mongo_propose_write"}:
                        if result.get("status") in {
                            "awaiting_approval",
                            "auto_committed",
                        }:
                            pending_previews.append(result)
                    # Nova/Converse expects json content blocks
                    result_blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"json": json.loads(json.dumps(result, default=str))}],
                                "status": "success",
                            }
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    result_blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": tool_use_id,
                                "content": [{"json": {"error": str(exc)}}],
                                "status": "error",
                            }
                        }
                    )
            messages.append({"role": "user", "content": result_blocks})
            continue

        assistant_text = "\n".join(text_parts).strip() or (
            "The change is waiting in Preview."
            if pending_previews
            else "Done."
        )
        _persist_message(sid, "assistant", assistant_text)
        return {
            "session_id": sid,
            "message": assistant_text,
            "previews": pending_previews,
            "stop_reason": stop,
            "model": settings.bedrock_model_id,
        }

    fallback = (
        "I hit the tool-round limit. Review any pending previews, or ask me to continue."
    )
    _persist_message(sid, "assistant", fallback)
    return {
        "session_id": sid,
        "message": fallback,
        "previews": pending_previews,
        "stop_reason": "max_tool_rounds",
        "model": settings.bedrock_model_id,
    }
