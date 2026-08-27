"""Action intent embeddings for semantic audit / past-write memory."""

from __future__ import annotations

from typing import Any

from sentinel.agent.bedrock import embed, vector_literal
from sentinel.db import connect
from sentinel.tenancy import require_user_id


def embed_action_intent(action_id: str, text: str) -> None:
    """Store Titan embedding for a previewed/committed action."""
    vector = embed(text)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sentinel.actions
            SET intent_embedding = %s::vector
            WHERE id = %s
            """,
            (vector_literal(vector), action_id),
        )


def search_similar_actions(
    query: str,
    *,
    limit: int = 5,
    statuses: tuple[str, ...] = ("committed", "previewed"),
) -> list[dict[str, Any]]:
    """Cosine search over past action intents (distributed VECTOR index)."""
    vector = embed(query)
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.request, a.sql_text, a.kind, a.risk, a.status,
                   a.rows_affected, a.created_at,
                   a.intent_embedding <=> %s::vector AS dist
            FROM sentinel.actions a
            JOIN sentinel.sessions s ON s.id = a.session_id
            WHERE a.intent_embedding IS NOT NULL
              AND a.status = ANY(%s)
              AND s.user_id = %s
            ORDER BY a.intent_embedding <=> %s::vector
            LIMIT %s
            """,
            (
                vector_literal(vector),
                list(statuses),
                require_user_id(),
                vector_literal(vector),
                limit,
            ),
        )
        rows = list(cur.fetchall())
    return [
        {
            "id": str(r["id"]),
            "request": r["request"],
            "sql_text": r["sql_text"],
            "kind": r["kind"],
            "risk": r["risk"],
            "status": r["status"],
            "rows_affected": r["rows_affected"],
            "dist": float(r["dist"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def format_actions_for_prompt(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "No similar past actions found."
    lines = [
        "Similar past actions (history only — do not mention these IDs, "
        "do not ask the user to approve them, propose a NEW write for this request):"
    ]
    for i, action in enumerate(actions, start=1):
        lines.append(
            f"{i}. [{action['status']}/{action['risk']}] "
            f"{action['sql_text'][:160]} (dist={action['dist']:.3f})"
        )
    return "\n".join(lines)
