"""Rule library: create, retrieve, and list procedural memory."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sentinel.agent.bedrock import embed, vector_literal
from sentinel.db import connect
from sentinel.tenancy import ensure_tenant_columns, require_user_id


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
    uid = require_user_id()
    ensure_tenant_columns()

    with connect() as conn, conn.cursor() as cur:
        # Near-duplicate check against existing active rules
        cur.execute(
            """
            SELECT id, body, status, embedding <=> %s::vector AS dist
            FROM sentinel.rules
            WHERE status IN ('probation', 'trusted')
              AND embedding IS NOT NULL
              AND user_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT 3
            """,
            (vector_literal(vector), uid, vector_literal(vector)),
        )
        near = list(cur.fetchall())
        duplicates = [r for r in near if float(r["dist"]) < 0.08]

        cur.execute(
            """
            INSERT INTO sentinel.rules (
                id, body, trigger_text, status, source_message_id, embedding, user_id
            ) VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
            RETURNING id, body, trigger_text, status, created_at
            """,
            (
                rule_id,
                body.strip(),
                trigger_text.strip(),
                status,
                source_message_id,
                vector_literal(vector),
                uid,
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
              AND user_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector_literal(vector), require_user_id(), vector_literal(vector), limit),
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
                WHERE status = %s AND user_id = %s
                ORDER BY created_at DESC
                """,
                (status, require_user_id()),
            )
        else:
            cur.execute(
                """
                SELECT id, body, trigger_text, status, created_at, status_changed_at
                FROM sentinel.rules
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (require_user_id(),),
            )
        return [dict(r) for r in cur.fetchall()]


def get_rule(rule_id: str) -> dict[str, Any] | None:
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, body, trigger_text, status, created_at, status_changed_at
            FROM sentinel.rules WHERE id = %s AND user_id = %s
            """,
            (rule_id, require_user_id()),
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
    lines = ["Trusted company rules you MUST follow (vector-retrieved):"]
    for i, rule in enumerate(rules, start=1):
        dist = f" dist={rule['dist']:.3f}" if "dist" in rule else ""
        lines.append(f"{i}. [{rule['id'][:8]}{dist}] {rule['body']}")
    return "\n".join(lines)


def search_rules(
    query: str,
    *,
    limit: int = 10,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search across rules (any status unless filtered)."""
    vector = embed(query)
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        if status:
            cur.execute(
                """
                SELECT id, body, trigger_text, status, created_at, status_changed_at,
                       embedding <=> %s::vector AS dist
                FROM sentinel.rules
                WHERE embedding IS NOT NULL AND status = %s AND user_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    vector_literal(vector),
                    status,
                    require_user_id(),
                    vector_literal(vector),
                    limit,
                ),
            )
        else:
            cur.execute(
                """
                SELECT id, body, trigger_text, status, created_at, status_changed_at,
                       embedding <=> %s::vector AS dist
                FROM sentinel.rules
                WHERE embedding IS NOT NULL AND user_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    vector_literal(vector),
                    require_user_id(),
                    vector_literal(vector),
                    limit,
                ),
            )
        rows = list(cur.fetchall())
    return [
        {
            "id": str(r["id"]),
            "body": r["body"],
            "trigger_text": r["trigger_text"],
            "status": r["status"],
            "created_at": r["created_at"],
            "status_changed_at": r["status_changed_at"],
            "dist": float(r["dist"]),
        }
        for r in rows
    ]


def similar_rules(rule_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT embedding FROM sentinel.rules WHERE id = %s AND user_id = %s",
            (rule_id, require_user_id()),
        )
        row = cur.fetchone()
        if row is None or row["embedding"] is None:
            return []
        cur.execute(
            """
            SELECT id, body, trigger_text, status,
                   embedding <=> %s::vector AS dist
            FROM sentinel.rules
            WHERE id <> %s AND embedding IS NOT NULL AND user_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (row["embedding"], rule_id, require_user_id(), row["embedding"], limit),
        )
        rows = list(cur.fetchall())
    return [
        {
            "id": str(r["id"]),
            "body": r["body"],
            "trigger_text": r["trigger_text"],
            "status": r["status"],
            "dist": float(r["dist"]),
        }
        for r in rows
    ]


def update_rule(
    rule_id: str,
    *,
    body: str | None = None,
    trigger_text: str | None = None,
) -> dict[str, Any]:
    """Edit rule text and re-embed with Titan."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, trigger_text, status FROM sentinel.rules WHERE id = %s AND user_id = %s",
            (rule_id, require_user_id()),
        )
        existing = cur.fetchone()
        if existing is None:
            raise ValueError("rule not found")

        new_body = body.strip() if body is not None else existing["body"]
        new_trigger = (
            trigger_text.strip()
            if trigger_text is not None
            else existing["trigger_text"]
        )
        if not new_body or not new_trigger:
            raise ValueError("body and trigger_text are required")

        vector = embed(f"{new_trigger}\n{new_body}")
        # Editing a trusted rule demotes it back to probation — must re-prove.
        new_status = existing["status"]
        if existing["status"] == "trusted" and (
            new_body != existing["body"] or new_trigger != existing["trigger_text"]
        ):
            new_status = "probation"

        cur.execute(
            """
            UPDATE sentinel.rules
            SET body = %s,
                trigger_text = %s,
                embedding = %s::vector,
                status = %s,
                status_changed_at = CASE
                    WHEN %s <> status THEN now() ELSE status_changed_at
                END
            WHERE id = %s AND user_id = %s
            RETURNING id, body, trigger_text, status, created_at, status_changed_at
            """,
            (
                new_body,
                new_trigger,
                vector_literal(vector),
                new_status,
                new_status,
                rule_id,
                require_user_id(),
            ),
        )
        row = cur.fetchone()

        cur.execute(
            """
            SELECT id, body, status, embedding <=> %s::vector AS dist
            FROM sentinel.rules
            WHERE id <> %s
              AND status IN ('probation', 'trusted')
              AND embedding IS NOT NULL
              AND user_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT 3
            """,
            (vector_literal(vector), rule_id, require_user_id(), vector_literal(vector)),
        )
        near = [
            {
                "id": str(r["id"]),
                "body": r["body"],
                "status": r["status"],
                "dist": float(r["dist"]),
            }
            for r in cur.fetchall()
            if float(r["dist"]) < 0.12
        ]

    return {"rule": dict(row), "near_duplicates": near, "demoted": new_status == "probation" and existing["status"] == "trusted"}


def set_rule_status(rule_id: str, status: str, *, reason: str | None = None) -> dict[str, Any]:
    """Manual status change for retire/reject/probation. Cannot force trusted."""
    if status not in {"probation", "rejected", "retired"}:
        raise ValueError(
            "manual status must be probation, rejected, or retired "
            "(trusted only via evaluation)"
        )
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, status FROM sentinel.rules WHERE id = %s AND user_id = %s",
            (rule_id, require_user_id()),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("rule not found")
        from_status = row["status"]
        cur.execute(
            """
            UPDATE sentinel.rules
            SET status = %s, status_changed_at = now()
            WHERE id = %s AND user_id = %s
            RETURNING id, body, trigger_text, status, created_at, status_changed_at
            """,
            (status, rule_id, require_user_id()),
        )
        updated = cur.fetchone()
        cur.execute(
            """
            INSERT INTO sentinel.rule_decisions (
                rule_id, batch_id, from_status, to_status,
                n_with, n_without, pass_with, pass_without,
                delta, ci_low, ci_high
            ) VALUES (
                %s, NULL, %s, %s,
                0, 0, 0, 0,
                0, 0, 0
            )
            """,
            (rule_id, from_status, status),
        )
    return {
        "rule": dict(updated),
        "from_status": from_status,
        "to_status": status,
        "reason": reason or "manual",
    }


def reembed_rule(rule_id: str) -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, trigger_text FROM sentinel.rules WHERE id = %s AND user_id = %s",
            (rule_id, require_user_id()),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("rule not found")
        vector = embed(f"{row['trigger_text']}\n{row['body']}")
        cur.execute(
            """
            UPDATE sentinel.rules
            SET embedding = %s::vector
            WHERE id = %s AND user_id = %s
            RETURNING id, body, trigger_text, status
            """,
            (vector_literal(vector), rule_id, require_user_id()),
        )
        updated = cur.fetchone()
    return {"rule": dict(updated), "reembedded": True}
