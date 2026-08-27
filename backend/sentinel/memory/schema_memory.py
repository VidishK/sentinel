"""Schema catalog embeddings — semantic retrieval over company table docs."""

from __future__ import annotations

from typing import Any

from sentinel.agent.bedrock import embed, vector_literal
from sentinel.db import connect
from sentinel.tenancy import require_user_id

# Hand-authored blurbs so Titan has real semantics to index.
SCHEMA_DOCS: list[dict[str, str]] = [
    {
        "object_name": "company.customers",
        "kind": "table",
        "content": (
            "Customers master list. Columns: id, name, email, country, is_test, created_at. "
            "Always exclude is_test = true unless the user asks about test accounts."
        ),
    },
    {
        "object_name": "company.products",
        "kind": "table",
        "content": (
            "Product catalog. Columns: id, name, category, active. "
            "Join to prices for current amount and price_history for past changes."
        ),
    },
    {
        "object_name": "company.prices",
        "kind": "table",
        "content": (
            "Current product prices. Columns: product_id, amount, updated_at. "
            "When updating a price, also insert into price_history."
        ),
    },
    {
        "object_name": "company.price_history",
        "kind": "table",
        "content": (
            "Historical price changes. Columns: id, product_id, amount, recorded_at. "
            "Append-only audit of price edits."
        ),
    },
    {
        "object_name": "company.orders",
        "kind": "table",
        "content": (
            "Customer orders. Columns: id, customer_id, status, total_amount, created_at. "
            "Watch for duplicate orders; join customers and check is_test."
        ),
    },
    {
        "object_name": "company.order_items",
        "kind": "table",
        "content": (
            "Line items on orders. Columns: id, order_id, product_id, qty, unit_price."
        ),
    },
    {
        "object_name": "company.refunds",
        "kind": "table",
        "content": (
            "Refunds against orders. Columns: id, order_id, amount, reason, created_at. "
            "Subtract refunds from revenue calculations."
        ),
    },
]


def ensure_schema_docs_table() -> None:
    from sentinel.tenancy import ensure_tenant_columns

    ensure_tenant_columns()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sentinel.schema_docs (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                object_name STRING NOT NULL UNIQUE,
                kind        STRING NOT NULL,
                content     STRING NOT NULL,
                embedding   VECTOR(1024),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

def reindex_schema_docs() -> dict[str, Any]:
    """Embed and upsert all schema blurbs with Titan."""
    ensure_schema_docs_table()
    upserted = 0
    with connect() as conn, conn.cursor() as cur:
        for doc in SCHEMA_DOCS:
            vector = embed(f"{doc['object_name']}\n{doc['content']}")
            cur.execute(
                """
                UPSERT INTO sentinel.schema_docs (object_name, kind, content, embedding, updated_at)
                VALUES (%s, %s, %s, %s::vector, now())
                """,
                (
                    doc["object_name"],
                    doc["kind"],
                    doc["content"],
                    vector_literal(vector),
                ),
            )
            upserted += 1
    return {"upserted": upserted}


def search_schema_docs(query: str, *, limit: int = 4) -> list[dict[str, Any]]:
    ensure_schema_docs_table()
    vector = embed(query)
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT object_name, kind, content,
                   embedding <=> %s::vector AS dist
            FROM sentinel.schema_docs
            WHERE embedding IS NOT NULL
              AND (user_id = %s OR object_name LIKE %s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (
                vector_literal(vector),
                require_user_id(),
                f"{require_user_id()}:%",
                vector_literal(vector),
                limit,
            ),
        )
        rows = list(cur.fetchall())
    if not rows:
        # First-run fallback before reindex.
        return [
            {
                "object_name": d["object_name"],
                "kind": d["kind"],
                "content": d["content"],
                "dist": None,
            }
            for d in SCHEMA_DOCS[:limit]
        ]
    return [
        {
            "object_name": r["object_name"],
            "kind": r["kind"],
            "content": r["content"],
            "dist": float(r["dist"]),
        }
        for r in rows
    ]


def format_schema_for_prompt(docs: list[dict[str, Any]]) -> str:
    if not docs:
        return "No schema memory retrieved."
    lines = ["Relevant schema memory (vector retrieval):"]
    for i, doc in enumerate(docs, start=1):
        dist = f", dist={doc['dist']:.3f}" if doc.get("dist") is not None else ""
        lines.append(f"{i}. {doc['object_name']}{dist}: {doc['content']}")
    return "\n".join(lines)
