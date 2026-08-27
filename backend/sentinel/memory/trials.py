"""A/B trial harness and statistical promotion gate for rules."""

from __future__ import annotations

import json
import math
import re
import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import boto3

from sentinel.config import get_settings
from sentinel.db import connect


def _qualify(sql: str) -> str:
    from sentinel.agent.tools import _qualify_company_tables

    return _qualify_company_tables(sql)

def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0, 0.0
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    spread = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    low = (centre - spread) / denom
    high = (centre + spread) / denom
    return max(0.0, low), min(1.0, high)


def _runtime():
    settings = get_settings()
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def _ask_for_sql(prompt: str, rules_block: str | None) -> str:
    """Ask the model for a single SELECT. No tools — just SQL text."""
    settings = get_settings()
    system = (
        "You write CockroachDB SQL for the company schema. "
        "Reply with ONLY a single SELECT statement. No markdown, no explanation. "
        "Always qualify tables as company.<name>."
    )
    if rules_block:
        system += "\n\nYou must obey these rules:\n" + rules_block

    response = _runtime().converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 300, "temperature": 0.0},
    )
    text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            text += block["text"]
    sql = text.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    sql = sql.strip().rstrip(";")
    # If the model added chatter, take the first SELECT... line block
    match = re.search(r"(SELECT\b[\s\S]+)", sql, re.IGNORECASE)
    if match:
        sql = match.group(1).strip().rstrip(";")
    return _qualify(sql)


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _values_match(actual: Any, expected: Any, *, tol: float = 0.01) -> bool:
    actual = _normalize(actual)
    expected = _normalize(expected)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) <= tol
    return actual == expected


def _run_task_once(task: dict[str, Any], rules_block: str | None) -> bool:
    expected = task["expected"]
    if isinstance(expected, str):
        expected = json.loads(expected)
    expected_value = expected.get("value") if isinstance(expected, dict) else expected

    try:
        sql = _ask_for_sql(task["prompt"], rules_block)
        if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
            return False
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            if not row:
                return False
            # Prefer a column named value, else first column
            actual = row.get("value", next(iter(row.values())))
        return _values_match(actual, expected_value)
    except Exception:  # noqa: BLE001
        return False


def run_ab_trials(rule_id: str, *, task_family: str | None = None) -> dict[str, Any]:
    """Run the eval set with and without the rule; persist every trial row."""
    batch_id = str(uuid4())

    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, body, trigger_text, status FROM sentinel.rules WHERE id = %s",
            (rule_id,),
        )
        rule = cur.fetchone()
        if rule is None:
            raise ValueError(f"rule not found: {rule_id}")
        if task_family:
            cur.execute(
                """
                SELECT id, task_family, kind, prompt, expected, checker
                FROM sentinel.tasks
                WHERE kind = 'read' AND task_family = %s
                ORDER BY task_family, prompt
                """,
                (task_family,),
            )
        else:
            cur.execute(
                """
                SELECT id, task_family, kind, prompt, expected, checker
                FROM sentinel.tasks
                WHERE kind = 'read'
                ORDER BY task_family, prompt
                """
            )
        tasks = list(cur.fetchall())

    if not tasks:
        raise ValueError("no evaluation tasks loaded; apply backend/sql/003_eval_tasks.sql")

    rules_block = f"- {rule['body']}"
    results: list[dict[str, Any]] = []

    for task in tasks:
        t0 = time.perf_counter()
        passed_with = _run_task_once(task, rules_block)
        latency_with = int((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        passed_without = _run_task_once(task, None)
        latency_without = int((time.perf_counter() - t0) * 1000)

        results.append(
            {
                "task_id": str(task["id"]),
                "prompt": task["prompt"],
                "with_rule": passed_with,
                "without_rule": passed_without,
            }
        )

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sentinel.rule_trials
                    (rule_id, task_id, batch_id, with_rule, passed, latency_ms)
                VALUES (%s, %s, %s, true, %s, %s)
                """,
                (rule_id, str(task["id"]), batch_id, passed_with, latency_with),
            )
            cur.execute(
                """
                INSERT INTO sentinel.rule_trials
                    (rule_id, task_id, batch_id, with_rule, passed, latency_ms)
                VALUES (%s, %s, %s, false, %s, %s)
                """,
                (rule_id, str(task["id"]), batch_id, passed_without, latency_without),
            )

    n = len(results)
    pass_with = sum(1 for r in results if r["with_rule"])
    pass_without = sum(1 for r in results if r["without_rule"])
    # Paired wins: tasks the rule fixed (passed with, failed without)
    wins = sum(1 for r in results if r["with_rule"] and not r["without_rule"])
    ci_low, ci_high = wilson_interval(wins, n)
    delta = (pass_with - pass_without) / n if n else 0.0

    return {
        "rule_id": rule_id,
        "batch_id": batch_id,
        "n_with": n,
        "n_without": n,
        "pass_with": pass_with,
        "pass_without": pass_without,
        "wins": wins,
        "delta": delta,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "results": results,
    }


def decide_promotion(rule_id: str, trial_summary: dict[str, Any]) -> dict[str, Any]:
    """Promote or reject inside one transaction with the evidence row.

    Gate: promote only if the rule produced at least one paired win and the
    Wilson lower bound on the win-rate is > 0. That means we are confident the
    rule helped on some tasks, not that it helped by luck on noise.
    """
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM sentinel.rules WHERE id = %s",
            (rule_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"rule not found: {rule_id}")
        from_status = row["status"]

    promote = (
        trial_summary["wins"] > 0
        and trial_summary["ci_low"] > 0.0
        and trial_summary["pass_with"] > trial_summary["pass_without"]
    )
    to_status = "trusted" if promote else "rejected"

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sentinel.rules
            SET status = %s, status_changed_at = now()
            WHERE id = %s
            """,
            (to_status, rule_id),
        )
        cur.execute(
            """
            INSERT INTO sentinel.rule_decisions (
                rule_id, batch_id, from_status, to_status,
                n_with, n_without, pass_with, pass_without,
                delta, ci_low, ci_high
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id, to_status, delta, ci_low, ci_high, decided_at
            """,
            (
                rule_id,
                trial_summary["batch_id"],
                from_status,
                to_status,
                trial_summary["n_with"],
                trial_summary["n_without"],
                trial_summary["pass_with"],
                trial_summary["pass_without"],
                trial_summary["delta"],
                trial_summary["ci_low"],
                trial_summary["ci_high"],
            ),
        )
        decision = dict(cur.fetchone())

    return {
        "promoted": promote,
        "from_status": from_status,
        "to_status": to_status,
        "decision": decision,
        "trial": {
            k: trial_summary[k]
            for k in (
                "batch_id",
                "n_with",
                "n_without",
                "pass_with",
                "pass_without",
                "wins",
                "delta",
                "ci_low",
                "ci_high",
            )
        },
    }


def evaluate_and_decide(rule_id: str, *, task_family: str | None = None) -> dict[str, Any]:
    summary = run_ab_trials(rule_id, task_family=task_family)
    decision = decide_promotion(rule_id, summary)
    return {**decision, "results": summary["results"]}
