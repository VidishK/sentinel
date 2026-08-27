"""Tools the chat agent is allowed to call.

Reads run against the active data source. Writes are rehearsed first.
Bounded low-risk writes may auto-commit; medium/high still wait in Preview.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

from sentinel.db import connect
from sentinel.memory.actions import embed_action_intent
from sentinel.safety.policy import (
    PolicyViolation,
    enforce_write_proposal,
    get_policy,
    should_auto_apply,
)
from sentinel.safety.preview import approve_and_commit, preview_sql

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
                "List tables/collections on the active data source "
                "(Cockroach, Postgres, or MongoDB). Call this before writing queries."
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
                "Describe columns or fields for a table/collection on the active source. "
                "Pass customers, orders, or a qualified name like public.users."
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
                "Run a read-only SELECT on the active SQL source. "
                "Never use it for INSERT/UPDATE/DELETE. For MongoDB use mongo_find."
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
                "Propose a SQL write on the active SQL source. The statement is rehearsed "
                "inside a rolled-back transaction. Bounded low-risk writes may auto-commit; "
                "medium/high risk wait for human approval. Always call this instead of "
                "running writes yourself."
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
                "Save a how-to rule into probation memory. Use when the user "
                "corrects you or states a durable convention. The rule is NOT trusted "
                "until it passes A/B evaluation."
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
    {
        "toolSpec": {
            "name": "mongo_find",
            "description": (
                "Read documents from a MongoDB collection on the active source. "
                "filter_json is a Mongo filter object as JSON, e.g. {\"status\":\"open\"}."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string"},
                        "filter_json": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["collection"],
                    "additionalProperties": False,
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "mongo_propose_write",
            "description": (
                "Propose a MongoDB updateMany/deleteMany on one collection. "
                "Low-risk single-document updates may auto-apply; broader writes wait."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "description": "update or delete",
                        },
                        "filter_json": {"type": "string"},
                        "update_json": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["collection", "operation", "filter_json", "rationale"],
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
            "SQL may not touch sentinel/system catalogs"
        )


def _qualify_company_tables(sql: str) -> str:
    """Rewrite bare table names from the active catalog (demo or connected)."""
    patterns = [
        r"\bFROM\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bJOIN\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bINTO\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bUPDATE\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
        r"\bTABLE\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
    ]
    aliases: dict[str, str] = {}
    try:
        from sentinel.connectors.catalog import allowed_objects

        by_name: dict[str, list[str]] = {}
        for qualified in allowed_objects():
            by_name.setdefault(qualified.split(".")[-1].lower(), []).append(qualified)
        for name, matches in by_name.items():
            if len(matches) == 1:
                aliases[name] = matches[0]
        if not aliases:
            aliases = {name: f"company.{name}" for name in _COMPANY_TABLES}
    except Exception:  # noqa: BLE001
        aliases = {name: f"company.{name}" for name in _COMPANY_TABLES}

    out = sql
    for pat in patterns:

        def _sub(m: re.Match[str], names: dict[str, str] = aliases) -> str:
            full = m.group(0)
            table = m.group(1)
            if "." in full:
                return full
            mapped = names.get(table.lower())
            if mapped:
                return full.replace(table, mapped, 1)
            return full

        out = re.sub(pat, _sub, out, flags=re.IGNORECASE)
    return out


def list_tables(_: dict[str, Any]) -> dict[str, Any]:
    from sentinel.connectors.catalog import get_active

    active = get_active()
    catalog = active.get("catalog") or {}
    if isinstance(catalog, str):
        catalog = json.loads(catalog)
    objects = catalog.get("objects") or []
    return {
        "source": active.get("name"),
        "engine": active.get("engine"),
        "tables": [o.get("qualified") for o in objects],
        "note": "Query only objects listed here. Refresh the source catalog if this looks stale.",
    }


def describe_table(args: dict[str, Any]) -> dict[str, Any]:
    from sentinel.connectors.catalog import get_active

    table = str(args.get("table", "")).strip()
    if not table:
        raise ValueError("table is required")
    active = get_active()
    catalog = active.get("catalog") or {}
    if isinstance(catalog, str):
        catalog = json.loads(catalog)
    needle = table.lower().split(".")[-1]
    for obj in catalog.get("objects") or []:
        if obj.get("name", "").lower() == needle or obj.get("qualified", "").lower() == table.lower():
            return {
                "table": obj.get("qualified"),
                "engine": active["engine"],
                "columns": obj.get("columns") or [],
            }
    raise ValueError(f"unknown table on active source: {table}")


def run_readonly_sql(args: dict[str, Any]) -> dict[str, Any]:
    from sentinel.connectors.catalog import get_active

    active = get_active()
    if active["engine"] == "mongodb":
        raise ValueError("Active source is MongoDB. Use mongo_find instead of SQL.")
    sql = _qualify_company_tables(str(args.get("sql", "")).strip().rstrip(";"))
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        raise ValueError("run_readonly_sql only accepts SELECT statements")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b", sql, re.I):
        raise ValueError("write keywords are not allowed in run_readonly_sql")
    _assert_company_only(sql)

    preview = preview_sql(sql)
    if preview.error:
        raise ValueError(preview.error)
    return {
        "sql": sql,
        "row_count": preview.rows_affected,
        "rows": preview.result_rows[:50],
    }


def propose_write(
    args: dict[str, Any],
    *,
    session_id: str,
    user_request: str,
) -> dict[str, Any]:
    from sentinel.connectors.catalog import get_active

    active = get_active()
    if active["engine"] == "mongodb":
        raise ValueError("Active source is MongoDB. Use mongo_propose_write.")
    sql = _qualify_company_tables(str(args.get("sql", "")).strip().rstrip(";"))
    rationale = str(args.get("rationale", "")).strip()
    if not sql:
        raise ValueError("sql is required")
    if not rationale:
        raise ValueError("rationale is required for every write proposal")
    _assert_company_only(sql)
    try:
        enforce_write_proposal(sql)
    except PolicyViolation as exc:
        raise ValueError(str(exc)) from exc

    preview = preview_sql(sql)
    policy = get_policy()
    if (
        preview.rows_affected is not None
        and preview.rows_affected > policy.max_preview_rows
        and preview.error is None
    ):
        preview.error = (
            f"Blocked: would touch {preview.rows_affected} rows "
            f"(max {policy.max_preview_rows}). Narrow the WHERE clause."
        )
    action_id = str(uuid4())

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sentinel.actions (
                id, session_id, request, sql_text, kind, risk,
                risk_reasons, rows_affected, diff_summary, status, source_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s::jsonb, %s, %s
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
                str(active["id"]),
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

    # Titan embedding for semantic recall of past writes / audits.
    try:
        embed_action_intent(
            action_id,
            f"{user_request}\n{rationale}\n{preview.sql_text}",
        )
    except Exception:  # noqa: BLE001
        pass

    auto = (
        preview.error is None
        and should_auto_apply(preview.risk.level, preview.rows_affected)
    )
    if auto:
        try:
            commit = approve_and_commit(preview.sql_text)
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sentinel.actions
                    SET status = 'committed', committed_at = now()
                    WHERE id = %s
                    """,
                    (action_id,),
                )
                cur.execute(
                    """
                    INSERT INTO sentinel.audit_events
                        (action_id, event_type, actor, payload)
                    VALUES (%s, 'auto_committed', 'agent', %s::jsonb)
                    """,
                    (
                        action_id,
                        json.dumps(
                            {"reason": "low-risk bounded write", **commit},
                            default=str,
                        ),
                    ),
                )
            return {
                "action_id": action_id,
                "status": "auto_committed",
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
                "error": None,
                "message": (
                    f"Applied. Low-risk write, {preview.rows_affected} row(s). "
                    "Logged in Audit."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "action_id": action_id,
                "status": "failed",
                "sql": preview.sql_text,
                "rationale": rationale,
                "risk": {
                    "level": preview.risk.level,
                    "reasons": preview.risk.reasons,
                    "requires_backup": False,
                },
                "rows_affected": preview.rows_affected,
                "error": str(exc),
                "message": f"Auto-apply failed: {exc}",
            }

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
            "Rehearsed, not committed. Waiting in Preview."
            if preview.error is None
            else f"Preview failed: {preview.error}"
        ),
    }


def mongo_find(args: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import urlparse

    from pymongo import MongoClient

    from sentinel.connectors.catalog import get_active

    active = get_active()
    if active["engine"] != "mongodb":
        raise ValueError("Active source is not MongoDB.")
    collection = str(args.get("collection", "")).strip()
    if not collection:
        raise ValueError("collection is required")
    try:
        filt = json.loads(str(args.get("filter_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("filter_json must be JSON") from exc
    limit = min(int(args.get("limit") or 20), 50)
    db_name = (urlparse(active["dsn"]).path or "").lstrip("/") or "test"
    client = MongoClient(active["dsn"], serverSelectionTimeoutMS=8000)
    try:
        docs = list(client[db_name][collection].find(filt).limit(limit))
        for doc in docs:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
        return {"collection": collection, "count": len(docs), "documents": docs}
    finally:
        client.close()


def mongo_propose_write(
    args: dict[str, Any],
    *,
    session_id: str,
    user_request: str,
) -> dict[str, Any]:
    from urllib.parse import urlparse

    from pymongo import MongoClient

    from sentinel.connectors.catalog import get_active

    active = get_active()
    if active["engine"] != "mongodb":
        raise ValueError("Active source is not MongoDB.")
    collection = str(args.get("collection", "")).strip()
    operation = str(args.get("operation", "update")).strip().lower()
    rationale = str(args.get("rationale", "")).strip()
    if operation not in {"update", "delete"}:
        raise ValueError("operation must be update or delete")
    try:
        filt = json.loads(str(args.get("filter_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("filter_json must be JSON") from exc
    if not filt:
        raise ValueError("Mongo writes require a non-empty filter.")
    update_doc: dict[str, Any] = {}
    if operation == "update":
        try:
            update_doc = json.loads(str(args.get("update_json") or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("update_json must be JSON") from exc
        if not update_doc:
            raise ValueError("update_json is required for update")
        if not any(str(k).startswith("$") for k in update_doc):
            update_doc = {"$set": update_doc}

    db_name = (urlparse(active["dsn"]).path or "").lstrip("/") or "test"
    client = MongoClient(active["dsn"], serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][collection]
        before = list(coll.find(filt).limit(20))
        matched = coll.count_documents(filt)
        for doc in before:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
    finally:
        client.close()

    risk_level = "low" if matched <= 1 else ("medium" if matched <= 25 else "high")
    action_id = str(uuid4())
    sql_text = json.dumps(
        {
            "engine": "mongodb",
            "collection": collection,
            "operation": operation,
            "filter": filt,
            "update": update_doc,
        },
        default=str,
    )
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sentinel.actions (
                id, session_id, request, sql_text, kind, risk,
                risk_reasons, rows_affected, diff_summary, status, source_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s::jsonb, %s, %s
            )
            """,
            (
                action_id,
                session_id,
                f"{user_request}\n\n{rationale}",
                sql_text,
                "write",
                risk_level,
                json.dumps([f"mongodb {operation} matched {matched}"]),
                matched,
                json.dumps({"before_sample": before[:5]}, default=str),
                "previewed",
                str(active["id"]),
            ),
        )

    if should_auto_apply(risk_level, matched):
        client = MongoClient(active["dsn"], serverSelectionTimeoutMS=8000)
        try:
            coll = client[db_name][collection]
            if operation == "delete":
                applied = coll.delete_many(filt).deleted_count
            else:
                applied = coll.update_many(filt, update_doc).modified_count
        finally:
            client.close()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sentinel.actions
                SET status = 'committed', committed_at = now(), rows_affected = %s
                WHERE id = %s
                """,
                (applied, action_id),
            )
            cur.execute(
                """
                INSERT INTO sentinel.audit_events
                    (action_id, event_type, actor, payload)
                VALUES (%s, 'auto_committed', 'agent', %s::jsonb)
                """,
                (
                    action_id,
                    json.dumps(
                        {
                            "reason": "low-risk bounded mongo write",
                            "rows_affected": applied,
                        },
                        default=str,
                    ),
                ),
            )
        return {
            "action_id": action_id,
            "status": "auto_committed",
            "rows_affected": applied,
            "message": f"Applied Mongo {operation} on {applied} document(s).",
        }

    return {
        "action_id": action_id,
        "status": "awaiting_approval",
        "sql": sql_text,
        "rationale": rationale,
        "risk": {
            "level": risk_level,
            "reasons": [f"mongodb {operation} matched {matched}"],
            "requires_backup": risk_level == "high",
        },
        "rows_affected": matched,
        "before_sample": before[:5],
        "message": "Rehearsed, not committed. Waiting in Preview.",
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
        "mongo_find": mongo_find,
    }
    if name == "propose_write":
        return propose_write(args, session_id=session_id, user_request=user_request)
    if name == "mongo_propose_write":
        return mongo_propose_write(
            args, session_id=session_id, user_request=user_request
        )
    if name not in handlers:
        raise ValueError(f"unknown tool: {name}")
    return handlers[name](args)
