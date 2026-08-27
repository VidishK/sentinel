"""Connected data sources (Cockroach / Postgres / Mongo) plus schema catalog.

Sentinel's control plane always stays on Cockroach (`DATABASE_URL`).
Operational data can be the demo `company` schema on that cluster, or a
user-connected Postgres / MongoDB. The agent introspects whatever is active
and embeds a summary into vector memory.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from sentinel.config import get_settings, normalize_source_dsn
from sentinel.db import connect
from sentinel.tenancy import ensure_tenant_columns, require_user_id

_SQL_ENGINES = {"cockroach", "postgres"}
_MONGO_ENGINES = {"mongodb"}


def ensure_connections_table() -> None:
    ensure_tenant_columns()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sentinel.connections (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name        STRING NOT NULL,
                engine      STRING NOT NULL,
                dsn         STRING NOT NULL,
                active      BOOL NOT NULL DEFAULT false,
                catalog     JSONB,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT valid_engine CHECK (
                    engine IN ('cockroach', 'postgres', 'mongodb')
                )
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE sentinel.actions
            ADD COLUMN IF NOT EXISTS source_id UUID
            """
        )
        cur.execute("SELECT count(*) AS n FROM sentinel.connections")
        # Legacy seed only when the table is empty. New accounts get their own
        # demo row in _ensure_user_source().
        if int(cur.fetchone()["n"]) == 0:
            settings = get_settings()
            cur.execute(
                """
                INSERT INTO sentinel.connections (name, engine, dsn, active, catalog)
                VALUES (%s, 'cockroach', %s, true, '{}'::jsonb)
                """,
                ("demo-company (Cockroach)", settings.database_url),
            )


def _parse_catalog(raw: Any) -> dict[str, Any]:
    catalog = raw or {}
    if isinstance(catalog, str):
        try:
            catalog = json.loads(catalog)
        except json.JSONDecodeError:
            catalog = {}
    return catalog if isinstance(catalog, dict) else {}


def _hydrate_catalog(row: dict[str, Any]) -> dict[str, Any]:
    """Introspect once if this source has no objects yet."""
    catalog = _parse_catalog(row.get("catalog"))
    row["catalog"] = catalog
    if catalog.get("objects"):
        return row
    try:
        filled = introspect(str(row["engine"]), str(row["dsn"]))
    except Exception:  # noqa: BLE001
        return row
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sentinel.connections
            SET catalog = %s::jsonb
            WHERE id = %s
            """,
            (json.dumps(filled, default=str), str(row["id"])),
        )
    row["catalog"] = filled
    return row


def _ensure_user_source() -> None:
    """Each account gets its own connection rows. Demo DSN is shared data."""
    uid = require_user_id()
    ensure_connections_table()
    settings = get_settings()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM sentinel.connections WHERE user_id = %s",
            (uid,),
        )
        if int(cur.fetchone()["n"]) > 0:
            return
        cur.execute(
            """
            INSERT INTO sentinel.connections
                (name, engine, dsn, active, catalog, user_id)
            VALUES (%s, 'cockroach', %s, true, '{}'::jsonb, %s)
            """,
            ("Shared demo (Cockroach)", settings.database_url, uid),
        )


def mask_dsn(dsn: str) -> str:
    try:
        parsed = urlparse(dsn)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}", ":***")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:  # noqa: BLE001
        pass
    return re.sub(r":([^:@/]+)@", ":***@", dsn)


def _public(row: dict[str, Any]) -> dict[str, Any]:
    catalog = _parse_catalog(row.get("catalog"))
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "engine": row["engine"],
        "dsn_masked": mask_dsn(row["dsn"]),
        "active": bool(row["active"]),
        "object_count": len(catalog.get("objects") or []),
        "catalog": catalog,
        "created_at": row.get("created_at"),
    }


def list_connections() -> list[dict[str, Any]]:
    _ensure_user_source()
    uid = require_user_id()
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, engine, dsn, active, catalog, created_at
            FROM sentinel.connections
            WHERE user_id = %s
            ORDER BY created_at
            """,
            (uid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    out = []
    for row in rows:
        catalog = _parse_catalog(row.get("catalog"))
        if row.get("active") and not catalog.get("objects"):
            row = _hydrate_catalog(row)
        out.append(_public(row))
    return out


def get_active() -> dict[str, Any]:
    _ensure_user_source()
    uid = require_user_id()
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, engine, dsn, active, catalog, created_at
            FROM sentinel.connections
            WHERE user_id = %s AND active = true
            ORDER BY created_at
            LIMIT 1
            """,
            (uid,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                SELECT id, name, engine, dsn, active, catalog, created_at
                FROM sentinel.connections
                WHERE user_id = %s
                ORDER BY created_at
                LIMIT 1
                """,
                (uid,),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("no data source configured")
        payload = _hydrate_catalog(dict(row))
        if payload.get("engine") in _SQL_ENGINES:
            payload["dsn"] = normalize_source_dsn(
                str(payload["engine"]), str(payload["dsn"])
            )
        return payload


def set_active(connection_id: str) -> dict[str, Any]:
    uid = require_user_id()
    ensure_connections_table()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM sentinel.connections
            WHERE id = %s AND user_id = %s
            """,
            (connection_id, uid),
        )
        if cur.fetchone() is None:
            raise ValueError("connection not found")
        cur.execute(
            "UPDATE sentinel.connections SET active = false WHERE user_id = %s",
            (uid,),
        )
        cur.execute(
            """
            UPDATE sentinel.connections SET active = true
            WHERE id = %s AND user_id = %s
            RETURNING id, name, engine, dsn, active, catalog, created_at
            """,
            (connection_id, uid),
        )
        return _public(dict(cur.fetchone()))


def delete_connection(connection_id: str) -> None:
    uid = require_user_id()
    ensure_connections_table()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT active FROM sentinel.connections
            WHERE id = %s AND user_id = %s
            """,
            (connection_id, uid),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("connection not found")
        if row["active"]:
            raise ValueError("cannot delete the active source; activate another first")
        cur.execute(
            """
            DELETE FROM sentinel.connections
            WHERE id = %s AND user_id = %s
            """,
            (connection_id, uid),
        )


def introspect_sql(dsn: str) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    with psycopg.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=15,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema NOT IN (
                    'pg_catalog', 'information_schema', 'crdb_internal',
                    'pg_extension', 'sentinel'
                )
                ORDER BY table_schema, table_name, ordinal_position
                """
            )
            grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
            for row in cur.fetchall():
                key = (row["table_schema"], row["table_name"])
                grouped.setdefault(key, []).append(
                    {
                        "name": row["column_name"],
                        "type": row["data_type"],
                    }
                )
            for (schema, table), cols in grouped.items():
                objects.append(
                    {
                        "schema": schema,
                        "name": table,
                        "qualified": f"{schema}.{table}",
                        "columns": cols,
                    }
                )
    return {"kind": "sql", "objects": objects}


def introspect_mongo(dsn: str) -> dict[str, Any]:
    from pymongo import MongoClient

    client = MongoClient(dsn, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
        parsed = urlparse(dsn)
        db_name = (parsed.path or "").lstrip("/") or "test"
        db = client[db_name]
        objects: list[dict[str, Any]] = []
        for name in db.list_collection_names():
            sample = db[name].find_one() or {}
            fields = sorted(str(k) for k in sample.keys())
            objects.append(
                {
                    "schema": db_name,
                    "name": name,
                    "qualified": f"{db_name}.{name}",
                    "columns": [{"name": f, "type": "bson"} for f in fields],
                }
            )
        return {"kind": "mongo", "database": db_name, "objects": objects}
    finally:
        client.close()


def introspect(engine: str, dsn: str) -> dict[str, Any]:
    if engine in _SQL_ENGINES:
        return introspect_sql(normalize_source_dsn(engine, dsn))
    if engine in _MONGO_ENGINES:
        return introspect_mongo(dsn)
    raise ValueError(f"unsupported engine: {engine}")


def add_connection(name: str, engine: str, dsn: str) -> dict[str, Any]:
    engine = engine.lower().strip()
    if engine not in _SQL_ENGINES | _MONGO_ENGINES:
        raise ValueError("engine must be cockroach, postgres, or mongodb")
    dsn = dsn.strip()
    if not dsn:
        raise ValueError("connection string is required")
    if engine in _SQL_ENGINES:
        dsn = normalize_source_dsn(engine, dsn)
    catalog = introspect(engine, dsn)
    conn_id = str(uuid4())
    uid = require_user_id()
    ensure_connections_table()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sentinel.connections SET active = false WHERE user_id = %s",
            (uid,),
        )
        cur.execute(
            """
            INSERT INTO sentinel.connections
                (id, name, engine, dsn, active, catalog, user_id)
            VALUES (%s, %s, %s, %s, true, %s::jsonb, %s)
            RETURNING id, name, engine, dsn, active, catalog, created_at
            """,
            (
                conn_id,
                name.strip() or f"{engine} source",
                engine,
                dsn,
                json.dumps(catalog, default=str),
                uid,
            ),
        )
        row = dict(cur.fetchone())
    _embed_catalog(catalog)
    return _public(row)


def refresh_active_catalog() -> dict[str, Any]:
    active = get_active()
    catalog = introspect(active["engine"], active["dsn"])
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sentinel.connections
            SET catalog = %s::jsonb
            WHERE id = %s
            RETURNING id, name, engine, dsn, active, catalog, created_at
            """,
            (json.dumps(catalog, default=str), str(active["id"])),
        )
        row = dict(cur.fetchone())
    _embed_catalog(catalog)
    return _public(row)


def _embed_catalog(catalog: dict[str, Any]) -> None:
    try:
        from sentinel.agent.bedrock import embed, vector_literal
        from sentinel.memory.schema_memory import ensure_schema_docs_table

        ensure_schema_docs_table()
        with connect() as conn, conn.cursor() as cur:
            for obj in (catalog.get("objects") or [])[:40]:
                cols = ", ".join(
                    f"{c['name']} ({c['type']})" for c in (obj.get("columns") or [])[:24]
                )
                content = f"{obj['qualified']}: {cols}"
                vector = embed(content)
                cur.execute(
                    """
                    UPSERT INTO sentinel.schema_docs
                        (object_name, kind, content, embedding, updated_at, user_id)
                    VALUES (%s, 'table', %s, %s::vector, now(), %s)
                    """,
                    (
                        f"{require_user_id()}:{obj['qualified']}",
                        content,
                        vector_literal(vector),
                        require_user_id(),
                    ),
                )
    except Exception:  # noqa: BLE001
        pass


def allowed_objects() -> list[str]:
    catalog = get_active().get("catalog") or {}
    if isinstance(catalog, str):
        catalog = json.loads(catalog)
    return [o["qualified"] for o in (catalog.get("objects") or [])]


def format_source_for_prompt() -> str:
    active = get_active()
    catalog = active.get("catalog") or {}
    if isinstance(catalog, str):
        catalog = json.loads(catalog)
    objects = catalog.get("objects") or []
    lines = [
        f"Active data source: {active['name']} ({active['engine']}).",
        "Use list_tables / describe_table against this source. "
        "Do not assume the demo company schema unless it is listed below.",
    ]
    for obj in objects[:30]:
        cols = ", ".join(c["name"] for c in (obj.get("columns") or [])[:12])
        lines.append(f"- {obj['qualified']}: {cols}")
    if not objects:
        lines.append("Catalog is empty — call list_tables first.")
    if active["engine"] == "mongodb":
        lines.append(
            "This is MongoDB. Use mongo_find to read and mongo_propose_write "
            "for updates. Do not generate SQL."
        )
    return "\n".join(lines)


def apply_mongo_write(payload_json: str) -> dict[str, Any]:
    from urllib.parse import urlparse

    from pymongo import MongoClient

    payload = json.loads(payload_json)
    if payload.get("engine") != "mongodb":
        raise ValueError("not a mongo action")
    active = get_active()
    if active["engine"] != "mongodb":
        raise ValueError("active source is not MongoDB")
    db_name = (urlparse(active["dsn"]).path or "").lstrip("/") or "test"
    client = MongoClient(active["dsn"], serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][payload["collection"]]
        filt = payload.get("filter") or {}
        if payload.get("operation") == "delete":
            applied = coll.delete_many(filt).deleted_count
        else:
            applied = coll.update_many(filt, payload.get("update") or {}).modified_count
    finally:
        client.close()
    return {
        "status": "committed",
        "rows_affected": applied,
        "sql": payload_json,
    }
