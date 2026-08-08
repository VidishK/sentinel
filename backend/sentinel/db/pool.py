from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from sentinel.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _pool


@contextmanager
def connect(*, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Borrow a connection from the pool.

    The preview engine needs explicit transaction control, so autocommit
    defaults to False. Callers that only run DDL or one-shot reads can
    pass autocommit=True.
    """
    pool = get_pool()
    with pool.connection() as conn:
        if conn.autocommit != autocommit:
            conn.autocommit = autocommit
        try:
            yield conn
            if not autocommit and not conn.closed:
                conn.commit()
        except Exception:
            if not autocommit and not conn.closed:
                conn.rollback()
            raise
