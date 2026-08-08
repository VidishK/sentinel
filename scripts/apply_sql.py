"""Apply a .sql file to the cluster, one statement at a time.

Statements run with autocommit rather than as one batch, because CockroachDB
rejects schema changes mixed with DML inside a single explicit transaction, and
these files deliberately mix both.

Run:  python scripts/apply_sql.py backend/sql/001_sentinel_schema.sql
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv


def split_statements(sql: str) -> list[str]:
    """Split on semicolons that fall outside string literals and comments."""
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_line_comment = False
    i = 0

    while i < len(sql):
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            current.append(char)
            if char == "\n":
                in_line_comment = False
        elif in_single:
            current.append(char)
            if char == "'":
                # '' is an escaped quote, not a terminator
                if nxt == "'":
                    current.append(nxt)
                    i += 1
                else:
                    in_single = False
        elif char == "-" and nxt == "-":
            in_line_comment = True
            current.append(char)
        elif char == "'":
            in_single = True
            current.append(char)
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1

    tail = "".join(current)
    if tail.strip():
        statements.append(tail)

    return [s.strip() for s in statements if s.strip() and not _only_comments(s)]


def _only_comments(statement: str) -> bool:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return False
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/apply_sql.py <path-to-sql>")
        return 2

    path = Path(argv[1])
    if not path.is_file():
        print(f"No such file: {path}")
        return 2

    load_dotenv()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
        return 2

    statements = split_statements(path.read_text())
    print(f"Applying {len(statements)} statements from {path}\n")

    with psycopg.connect(dsn, autocommit=True) as conn:
        for n, statement in enumerate(statements, start=1):
            preview = " ".join(statement.split())[:70]
            try:
                with conn.cursor() as cur:
                    cur.execute(statement)
                print(f"  {n:>3}. ok    {preview}")
            except psycopg.Error as exc:
                print(f"  {n:>3}. FAIL  {preview}")
                print(f"\n{exc}")
                return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
