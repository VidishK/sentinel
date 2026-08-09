"""Rule library: create, retrieve, and list procedural memory."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sentinel.agent.bedrock import embed, vector_literal
from sentinel.db import connect


def create_rule(
    body: str,
    trigger_text: str,
    *,
    source_message_id: str | None = None,
    status: str = "probation",
) -> dict[str, Any]:
    """Embed and store a rule. New rules start in probation by default."""
    if status not in {"probation", "trusted", "rejected", "retired"}:
        raise ValueError(f"invalid status: {status}")

    vector = embed(f"{trigger_text}\n{body}")
    rule_id = str(uuid4())

    with connect() as conn, conn.cursor() as cur:
        # Near-duplicate check against existing active rules
        cur.execute(
            """
            SELECT id, body, status, embedding <=> %s::vector AS dist
            FROM sentinel.rules
            WHERE status IN ('probation', 'trusted')
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT 3
            """,
            (vector_literal(vector), vector_literal(vector)),
        )
        near = list(cur.fetchall())
        duplicates = [r for r in near if float(r["dist"]) < 0.08]

        cur.execute(
            """
            INSERT INTO sentinel.rules (
                id, body, trigger_text, status, source_message_id, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s::vector)
            RETURNING id, body, trigger_text, status, created_at
            """,
            (
                rule_id,
                body.strip(),
                trigger_text.strip(),
                status,
                source_message_id,
                vector_literal(vector),
            ),
        )
        row = cur.fetchone()

    return {
        "rule": dict(row),
        "near_duplicates": [
            {"id": str(r["id"]), "body": r["body"], "status": r["status"], "dist": float(r["dist"])}
            for r in duplicates
        ],
    }


def search_trusted_rules(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Vector search over trusted rules only — what the agent may follow."""
    vector = embed(query)
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, body, trigger_text, status, created_at,
                   embedding <=> %s::vector AS dist
            FROM sentinel.rules
            WHERE status = 'trusted' AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector_literal(vector), vector_literal(vector), limit),
        )
        rows = list(cur.fetchall())
    return [
        {
            "id": str(r["id"]),
            "body": r["body"],
            "trigger_text": r["trigger_text"],
            "dist": float(r["dist"]),
        }
        for r in rows
    ]


def list_rules(status: str | None = None) -> list[dict[str, Any]]:
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        if status:
            cur.execute(
                """
                SELECT id, body, trigger_text, status, created_at, status_changed_at
                FROM sentinel.rules
                WHERE status = %s
                ORDER BY created_at DESC
                """,
                (status,),
            )
        else:
            cur.execute(
                """
                SELECT id, body, trigger_text, status, created_at, status_changed_at
                FROM sentinel.rules
                ORDER BY created_at DESC
                """
            )
        return [dict(r) for r in cur.fetchall()]


def get_rule(rule_id: str) -> dict[str, Any] | None:
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, body, trigger_text, status, created_at, status_changed_at
            FROM sentinel.rules WHERE id = %s
            """,
            (rule_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            """
            SELECT from_status, to_status, n_with, n_without, pass_with, pass_without,
                   delta, ci_low, ci_high, decided_at, batch_id
            FROM sentinel.rule_decisions
            WHERE rule_id = %s
            ORDER BY decided_at DESC
            """,
            (rule_id,),
        )
        decisions = [dict(d) for d in cur.fetchall()]
    result = dict(row)
    result["decisions"] = decisions
    return result


def format_rules_for_prompt(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "No trusted rules apply to this request yet."
    lines = ["Trusted company rules you MUST follow:"]
    for i, rule in enumerate(rules, start=1):
        lines.append(f"{i}. [{rule['id'][:8]}] {rule['body']}")
    return "\n".join(lines)
