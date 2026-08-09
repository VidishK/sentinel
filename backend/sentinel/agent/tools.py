"""Tools the chat agent is allowed to call.

Writes never execute here. They go through the preview engine and wait for
human approval. Reads are allowed, but only against the `company` schema.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

from sentinel.db import connect
from sentinel.safety.preview import preview_sql

_COMPANY_IDENT = re.compile(
    r"\b(company\.[A-Za-z_][\w]*|[A-Za-z_][\w]*)\b",
    re.IGNORECASE,
)
_FORBIDDEN = re.compile(
    r"\b(sentinel\.|information_schema\.|crdb_internal\.|pg_catalog\.)",
    re.IGNORECASE,
)


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "list_tables",
            "description": (
                "List tables in the company schema the agent may query or modify. "
                "Call this before writing SQL if you are unsure of table names."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "describe_table",
            "description": (
                "Describe columns for a company table. "
                "Pass the bare table name, e.g. 'customers' or 'orders'."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "description": "Table name without schema prefix",
                        }
                    },
                    "required": ["table"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "run_readonly_sql",
            "description": (
                "Run a read-only SELECT against the company schema and return rows. "
                "Use this to answer questions. Never use it for INSERT/UPDATE/DELETE."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "A single SELECT statement",
                        }
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "propose_write",
            "description": (
                "Propose a write (INSERT/UPDATE/DELETE/DDL). The statement is rehearsed "
                "inside a rolled-back transaction and the diff is shown to the user. "
                "Nothing is committed until the user explicitly approves. "
                "Always call this instead of running writes yourself."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "The write SQL to rehearse",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "Why this change is needed, in one sentence",
                        },
                    },
                    "required": ["sql", "rationale"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "propose_rule",
            "description": (
                "Save a company how-to rule into probation memory. Use when the user "
                "corrects you or states a durable convention (e.g. exclude test accounts, "
                "subtract refunds). The rule is NOT trusted until it passes A/B evaluation."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "body": {
                            "type": "string",
                            "description": "The rule text the agent should follow later",
                        },
                        "trigger_text": {
                            "type": "string",
                            "description": "When this rule applies, in plain language",
                        },
                    },
                    "required": ["body", "trigger_text"],
                    "additionalProperties": False,
                }
            },
        }
    },
]


_COMPANY_TABLES = {
    "customers",
    "products",
    "prices",
    "price_history",
    "orders",
    "order_items",
    "refunds",
}


def _assert_company_only(sql: str) -> None:
    if _FORBIDDEN.search(sql):
        raise ValueError(
            "SQL may only touch the company schema; "
            "sentinel/system catalogs are off limits"
        )
    # If a schema is mentioned, it must be company.
    for match in re.finditer(r"\b([A-Za-z_][\w]*)\.", sql):
        schema = match.group(1).lower()
        if schema not in {"company"}:
            raise ValueError(f"schema '{schema}' is not allowed; use company.*")


def _qualify_company_tables(sql: str) -> str:
    """Rewrite bare company table names to company.<table>.

    Models often omit the schema; we force the namespace so generated SQL
    cannot accidentally hit public/sentinel.
    """
    # Skip tokens that are already schema-qualified (company.customers).
    patterns = [
        r"\bFROM\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bJOIN\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bINTO\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bUPDATE\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bTABLE\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
    ]
    out = sql
    for pat in patterns:

        def _sub(m: re.Match[str]) -> str:
            full = m.group(0)
            table = m.group(1)
            if "." in full:
                return full
            if table.lower() in _COMPANY_TABLES:
                return full.replace(table, f"company.{table}", 1)
            return full

        out = re.sub(pat, _sub, out, flags=re.IGNORECASE)
    return out


def list_tables(_: dict[str, Any]) -> dict[str, Any]:
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'company'
            ORDER BY table_name
            """
        )
        tables = [row["table_name"] for row in cur.fetchall()]
    return {
        "tables": [f"company.{t}" for t in tables],
        "note": "Always qualify tables as company.<name> in SQL.",
    }


def describe_table(args: dict[str, Any]) -> dict[str, Any]:
    table = str(args.get("table", "")).strip().lower()
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        raise ValueError("invalid table name")
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'company' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        cols = list(cur.fetchall())
    if not cols:
        raise ValueError(f"unknown company table: {table}")
    return {"table": table, "columns": cols}


def run_readonly_sql(args: dict[str, Any]) -> dict[str, Any]:
    sql = _qualify_company_tables(str(args.get("sql", "")).strip().rstrip(";"))
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        raise ValueError("run_readonly_sql only accepts SELECT statements")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b", sql, re.I):
        raise ValueError("write keywords are not allowed in run_readonly_sql")
    _assert_company_only(sql)

    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())
    return {"sql": sql, "row_count": len(rows), "rows": rows[:50]}


def propose_write(
    args: dict[str, Any],
    *,
    session_id: str,
    user_request: str,
) -> dict[str, Any]:
    sql = _qualify_company_tables(str(args.get("sql", "")).strip().rstrip(";"))
    rationale = str(args.get("rationale", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    _assert_company_only(sql)

    preview = preview_sql(sql)
    action_id = str(uuid4())

    with connect() as conn, conn.cursor() as cur:
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
                action_id,
                session_id,
                user_request if not rationale else f"{user_request}\n\n{rationale}",
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
            VALUES (%s, %s, 'agent', %s::jsonb)
            """,
            (
                action_id,
                "previewed" if preview.error is None else "preview_failed",
                json.dumps(
                    {
                        "rationale": rationale,
                        "risk": preview.risk.level,
                        "reasons": preview.risk.reasons,
                        "rows_affected": preview.rows_affected,
                        "summary": preview.summary,
                        "error": preview.error,
                        "before": preview.before[:5],
                        "after": preview.after[:5],
                    },
                    default=str,
                ),
            ),
        )

    return {
        "action_id": action_id,
        "status": "awaiting_approval" if preview.error is None else "failed",
        "sql": preview.sql_text,
        "rationale": rationale,
        "risk": {
            "level": preview.risk.level,
            "reasons": preview.risk.reasons,
            "requires_backup": preview.risk.requires_backup,
        },
        "rows_affected": preview.rows_affected,
        "before_sample": preview.before[:5],
        "after_sample": preview.after[:5],
        "error": preview.error,
        "message": (
            "Change was rehearsed and rolled back. "
            "It is NOT committed. The user must approve action_id to apply it."
            if preview.error is None
            else f"Preview failed: {preview.error}"
        ),
    }


def propose_rule(args: dict[str, Any]) -> dict[str, Any]:
    from sentinel.memory.rules import create_rule

    body = str(args.get("body", "")).strip()
    trigger = str(args.get("trigger_text", "")).strip()
    if not body or not trigger:
        raise ValueError("body and trigger_text are required")
    created = create_rule(body, trigger, status="probation")
    return {
        "status": "probation",
        "message": (
            "Rule saved in probation. It will not be followed until it passes "
            "A/B evaluation against the held-out task set."
        ),
        **created,
    }


def dispatch(
    name: str,
    args: dict[str, Any],
    *,
    session_id: str,
    user_request: str,
) -> dict[str, Any]:
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "list_tables": list_tables,
        "describe_table": describe_table,
        "run_readonly_sql": run_readonly_sql,
        "propose_rule": propose_rule,
    }
    if name == "propose_write":
        return propose_write(args, session_id=session_id, user_request=user_request)
    if name not in handlers:
        raise ValueError(f"unknown tool: {name}")
    return handlers[name](args)
