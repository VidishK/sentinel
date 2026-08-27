"""ccloud backup guard.

Basic clusters cannot take on-demand backups from the CLI. Cockroach Cloud
still keeps automatic backups, and Cockroach itself can freeze a restore
point with cluster_logical_timestamp(). High-risk commits therefore:

1. Ask ccloud for the latest automatic backup (id + as_of_time).
2. Capture cluster_logical_timestamp() so the row can be inspected
   later with AS OF SYSTEM TIME.
3. Persist both on sentinel.actions.backup_id before the write commits.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from sentinel.config import get_settings
from sentinel.db import connect


class BackupError(RuntimeError):
    pass


def _run_ccloud(args: list[str]) -> str:
    ccloud = shutil.which("ccloud")
    if ccloud is None:
        raise BackupError("ccloud CLI is not installed")
    result = subprocess.run(
        [ccloud, *args],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "ccloud failed").strip()
        raise BackupError(err)
    return result.stdout


def list_cloud_backups() -> list[dict[str, Any]]:
    settings = get_settings()
    raw = _run_ccloud(
        [
            "cluster",
            "backup",
            "list",
            settings.ccloud_cluster_name,
            "-o",
            "json",
            "-q",
        ]
    )
    payload = json.loads(raw or "[]")
    if not isinstance(payload, list):
        return []
    return payload


def latest_cloud_backup() -> dict[str, Any] | None:
    backups = list_cloud_backups()
    if not backups:
        return None

    def _key(row: dict[str, Any]) -> str:
        return str(row.get("as_of_time") or row.get("id") or "")

    return sorted(backups, key=_key)[-1]


def capture_restore_point() -> dict[str, Any]:
    """Record the undo handle for a high-risk commit."""
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT cluster_logical_timestamp() AS ts")
        ts = str(cur.fetchone()["ts"])

    cloud: dict[str, Any] | None = None
    cloud_error: str | None = None
    try:
        cloud = latest_cloud_backup()
    except BackupError as exc:
        cloud_error = str(exc)

    backup_id = None
    if cloud and cloud.get("id"):
        backup_id = str(cloud["id"])
    else:
        backup_id = f"as-of:{ts}"

    return {
        "backup_id": backup_id,
        "cluster_logical_timestamp": ts,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "ccloud_cluster": get_settings().ccloud_cluster_name,
        "latest_automatic_backup": cloud,
        "ccloud_error": cloud_error,
    }


def guard_high_risk_commit(risk_level: str) -> dict[str, Any] | None:
    if str(risk_level).lower() != "high":
        return None
    point = capture_restore_point()
    if point.get("ccloud_error") and not point.get("latest_automatic_backup"):
        # Still have a CRDB timestamp; commit may proceed but the audit
        # records that the cloud backup list failed.
        point["warning"] = (
            "ccloud backup list failed; recorded cluster_logical_timestamp instead"
        )
    return point
