"""Day-0 cluster checks.

The design leans on three CockroachDB behaviours. If any of them do not work as
expected, that changes the architecture, so they get verified before anything
is built on top of them:

  1. VECTOR columns with a cosine vector index, and queries that actually use it
  2. Transactions that can capture a diff and then roll it back
  3. AS OF SYSTEM TIME, for reconstructing prior state

Run:  python scripts/validate_cluster.py
"""

from __future__ import annotations

import os
import random
import sys

import psycopg
from dotenv import load_dotenv

DIM = 1024
SAMPLE_ROWS = 500


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def random_vector(rng: random.Random) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(DIM)]


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}{f' - {detail}' if detail else ''}")
    return ok


def main() -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
        return 2

    rng = random.Random(0)
    results: list[bool] = []

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            print(f"Connected: {cur.fetchone()[0]}\n")

            # 1. Vector column + cosine index
            cur.execute("DROP TABLE IF EXISTS vec_check")
            cur.execute(
                f"""
                CREATE TABLE vec_check (
                    id        INT PRIMARY KEY,
                    embedding VECTOR({DIM}),
                    VECTOR INDEX vec_check_idx (embedding vector_cosine_ops)
                )
                """
            )
            results.append(check("VECTOR column and cosine index created", True))

            rows = [(i, vector_literal(random_vector(rng))) for i in range(SAMPLE_ROWS)]
            cur.executemany("INSERT INTO vec_check (id, embedding) VALUES (%s, %s)", rows)
            results.append(check(f"Inserted {SAMPLE_ROWS} embeddings", True))

            probe = vector_literal(random_vector(rng))
            cur.execute(
                "SELECT id FROM vec_check ORDER BY embedding <=> %s LIMIT 5",
                (probe,),
            )
            neighbours = [r[0] for r in cur.fetchall()]
            results.append(
                check("Nearest-neighbour query returns results", len(neighbours) == 5,
                      f"ids {neighbours}")
            )

            cur.execute(
                "EXPLAIN SELECT id FROM vec_check ORDER BY embedding <=> %s LIMIT 5",
                (probe,),
            )
            plan = "\n".join(row[0] for row in cur.fetchall())
            uses_index = "vec_check_idx" in plan
            results.append(
                check("Query plan uses the vector index", uses_index,
                      "falls back to a full scan" if not uses_index else "")
            )
            if not uses_index:
                print("\n--- plan ---\n" + plan + "\n------------\n")

            # 3. Time travel, and how far back it reaches
            cur.execute("SHOW ZONE CONFIGURATION FOR RANGE default")
            zone = "\n".join(str(r) for r in cur.fetchall())
            ttl_line = next(
                (line for line in zone.splitlines() if "gc.ttlseconds" in line), ""
            )
            print(f"\nRetention: {ttl_line.strip() or 'gc.ttlseconds not reported'}")

    # 2. Preview-and-rollback, on its own connection so autocommit is off
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS rollback_check (id INT PRIMARY KEY)")
            conn.commit()
            cur.execute("INSERT INTO rollback_check (id) VALUES (1), (2), (3)")
            cur.execute("SELECT count(*) FROM rollback_check")
            inside = cur.fetchone()[0]
            conn.rollback()
            cur.execute("SELECT count(*) FROM rollback_check")
            after = cur.fetchone()[0]
            results.append(
                check("Transaction preview then rollback leaves no trace",
                      inside == 3 and after == 0, f"{inside} inside, {after} after")
            )
            cur.execute("DROP TABLE IF EXISTS rollback_check")
            conn.commit()

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT count(*) FROM vec_check AS OF SYSTEM TIME '-10s'"
                )
                cur.fetchone()
                results.append(check("AS OF SYSTEM TIME works", True))
            except psycopg.Error as exc:
                results.append(check("AS OF SYSTEM TIME works", False, str(exc).strip()))
            cur.execute("DROP TABLE IF EXISTS vec_check")

    print()
    if all(results):
        print("All checks passed. The architecture is safe to build on.")
        return 0
    print("Some checks failed. Resolve these before building on top of them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
