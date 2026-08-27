"""Transaction rehearsal: run a write, capture the diff, always roll back.

Nothing in this module ever commits a change to `company.*`. The only way a
write becomes real is through approve_and_commit, which re-runs the SQL in a
fresh transaction after the user has seen the diff.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from sentinel.db import connect
from sentinel.safety.risk import RiskAssessment, classify_risk


def _same_sql_database(left: str, right: str) -> bool:
    from urllib.parse import urlsplit

    a, b = urlsplit(left), urlsplit(right)
    return (a.hostname, a.port, a.path.rstrip("/").lower()) == (
        b.hostname,
        b.port,
        b.path.rstrip("/").lower(),
    )


def _source_dsn() -> str | None:
    try:
        from sentinel.connectors.catalog import get_active
        from sentinel.config import get_settings

        active = get_active()
        if active.get("engine") not in {"cockroach", "postgres"}:
            return None
        dsn = str(active.get("dsn") or "")
        if dsn and not _same_sql_database(dsn, get_settings().database_url):
            return dsn
    except Exception:  # noqa: BLE001
        return None
    return None


@contextmanager
def _sql_conn(*, autocommit: bool) -> Iterator[psycopg.Connection]:
    dsn = _source_dsn()
    if dsn:
        with psycopg.connect(
            dsn,
            autocommit=autocommit,
            row_factory=dict_row,
            connect_timeout=15,
        ) as conn:
            yield conn
        return
    with connect(autocommit=autocommit) as conn:
        yield conn

_WRITE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|UPSERT|MERGE|ALTER|DROP|TRUNCATE|CREATE)\b",
    re.IGNORECASE,
)
_SELECT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


@dataclass
class PreviewResult:
    sql_text: str
    kind: str
    risk: RiskAssessment
    rows_affected: int
    before: list[dict[str, Any]] = field(default_factory=list)
    after: list[dict[str, Any]] = field(default_factory=list)
    result_rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _is_write(sql: str) -> bool:
    return bool(_WRITE.match(sql))


def _table_from_write(sql: str) -> str | None:
    """Best-effort extraction of the target table for before/after sampling."""
    patterns = [
        r"\bUPDATE\s+(?:ONLY\s+)?([a-zA-Z_][\w.]*)",
        r"\bDELETE\s+FROM\s+([a-zA-Z_][\w.]*)",
        r"\bINSERT\s+INTO\s+([a-zA-Z_][\w.]*)",
        r"\bUPSERT\s+INTO\s+([a-zA-Z_][\w.]*)",
    ]
    for pat in patterns:
        match = re.search(pat, sql, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _sample_rows(
    cur: psycopg.Cursor, table: str, limit: int = 20
) -> list[dict[str, Any]]:
    # Identifier quoting: only allow schema.table shaped names we just parsed.
    if not re.fullmatch(r"[A-Za-z_][\w.]*", table):
        return []
    cur.execute(f"SELECT * FROM {table} LIMIT %s", (limit,))
    return list(cur.fetchall())


def preview_sql(sql_text: str) -> PreviewResult:
    """Rehearse `sql_text` inside a transaction that is always rolled back."""
    sql = sql_text.strip().rstrip(";")
    if not sql:
        return PreviewResult(
            sql_text=sql,
            kind="read",
            risk=classify_risk(sql),
            rows_affected=0,
            error="empty SQL",
        )

    # Reads don't need a rehearsal — just run them.
    if _SELECT.match(sql) and not _is_write(sql):
        with _sql_conn(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = list(cur.fetchall()) if cur.description else []
        risk = classify_risk(sql)
        return PreviewResult(
            sql_text=sql,
            kind="read",
            risk=risk,
            rows_affected=len(rows),
            result_rows=rows[:50],
            summary={"returned": len(rows)},
        )

    table = _table_from_write(sql)
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    rows_affected = 0
    error: str | None = None

    with _sql_conn(autocommit=False) as conn:
        # Override the context manager's commit — we always roll back.
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                if table:
                    before = _sample_rows(cur, table)
                cur.execute(sql)
                rows_affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                if table:
                    after = _sample_rows(cur, table)
            conn.rollback()
        except Exception as exc:  # noqa: BLE001 - surface SQL errors to the UI
            conn.rollback()
            error = str(exc).strip()

    risk = classify_risk(sql, rows_affected if error is None else None)
    summary = {
        "rows_affected": rows_affected,
        "table": table,
        "before_sample": len(before),
        "after_sample": len(after),
        "risk": risk.level,
        "reasons": risk.reasons,
    }
    return PreviewResult(
        sql_text=sql,
        kind=risk.kind,
        risk=risk,
        rows_affected=rows_affected,
        before=before,
        after=after,
        summary=summary,
        error=error,
    )


def approve_and_commit(
    sql_text: str,
    *,
    conn: psycopg.Connection | None = None,
) -> dict[str, Any]:
    """Re-run SQL for real after approval or auto-apply."""
    sql = sql_text.strip().rstrip(";")

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows_affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return {"status": "committed", "rows_affected": rows_affected, "sql": sql}

    with _sql_conn(autocommit=False) as owned:
        with owned.cursor() as cur:
            cur.execute(sql)
            rows_affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        owned.commit()
    return {"status": "committed", "rows_affected": rows_affected, "sql": sql}


def diff_to_json(preview: PreviewResult) -> str:
    return json.dumps(
        {
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
        },
        default=str,
        indent=2,
    )
