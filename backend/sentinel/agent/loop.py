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

SYSTEM_PROMPT = """\
You are Sentinel, an AI database agent that companies can safely give write \
access to. You operate only on the `company` schema.

Hard rules:
1. Never claim a write was applied. Writes are only rehearsed. After propose_write, \
tell the user the action_id and that they must approve it.
2. For questions, use run_readonly_sql. Prefer counting/aggregating over dumping rows.
3. For changes, always call propose_write. Never invent a commit.
4. If you are unsure of the schema, call list_tables / describe_table first.
5. Watch for company landmines: test accounts (is_test), duplicate orders, \
refunds that must be subtracted from revenue, and price_history that should be \
updated when prices change.
6. Always use fully qualified names: company.customers, company.orders, etc.
7. When the user corrects you or states a durable convention, call propose_rule \
to save it into probation (not trusted yet).
8. Obey any Trusted company rules listed below.
9. Be concise. Show numbers. When a preview comes back, summarize risk, rows \
affected, and the before/after clearly. Stop once you can answer — do not keep \
calling tools after you have the result.
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
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sentinel.sessions (id, title)
            VALUES (%s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (new_id, title[:80]),
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
    system = SYSTEM_PROMPT + "\n\n" + format_rules_for_prompt(trusted)

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
                    if name == "propose_write":
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
            "I prepared a change preview. Please review it in the panel."
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
