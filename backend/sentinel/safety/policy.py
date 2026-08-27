"""Strict access policy for Sentinel.

The agent never gets open write access. Every write must clear these gates
before it can even be previewed, and high/medium risk still needs typed
confirmation at approve time.
"""

from __future__ import annotations

import re
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

_DDL = re.compile(
    r"\b(ALTER|DROP|TRUNCATE|CREATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_UPDATE_OR_DELETE = re.compile(r"\b(UPDATE|DELETE)\b", re.IGNORECASE)
_HAS_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_MULTI_STATEMENT = re.compile(r";\s*\S")

_ALLOWED_TABLES = {
    "customers",
    "products",
    "prices",
    "price_history",
    "orders",
    "order_items",
    "refunds",
}


@dataclass
class Policy:
    """Live guardrails. Defaults are strict on purpose."""

    mode: str = "strict"  # readonly | strict
    allow_writes: bool = True
    block_ddl: bool = True
    require_where: bool = True
    max_preview_rows: int = 500
    require_typed_confirm_for: list[str] = field(
        default_factory=lambda: ["medium", "high"]
    )
    allowed_schemas: list[str] = field(default_factory=lambda: ["company"])
    allowed_tables: list[str] = field(
        default_factory=lambda: sorted(_ALLOWED_TABLES)
    )
    confirm_phrase: str = "COMMIT"
    auto_apply_low_risk: bool = True
    auto_apply_max_rows: int = 25

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["summary"] = self.summary_lines()
        return data

    def summary_lines(self) -> list[str]:
        lines = [
            f"Mode: {self.mode}",
            "Writes allowed" if self.allow_writes and self.mode != "readonly" else "Read-only (writes blocked)",
            "DDL blocked" if self.block_ddl else "DDL allowed (high risk)",
            "UPDATE/DELETE require WHERE" if self.require_where else "WHERE not required",
            f"Typed '{self.confirm_phrase}' required for: {', '.join(self.require_typed_confirm_for) or 'none'}",
            (
                f"Auto-apply low-risk writes (≤{self.auto_apply_max_rows} rows)"
                if self.auto_apply_low_risk
                else "Every write waits for human approval"
            ),
            f"Writes limited to the connected catalog (plus demo {', '.join(self.allowed_schemas)})",
        ]
        return lines


_lock = threading.Lock()
_policy = Policy()
_by_user: dict[str, Policy] = {}


def _current_policy() -> Policy:
    from sentinel.tenancy import user_id

    uid = user_id()
    if not uid:
        return _policy
    if uid not in _by_user:
        _by_user[uid] = Policy()
    return _by_user[uid]


def get_policy() -> Policy:
    with _lock:
        return deepcopy(_current_policy())


def update_policy(patch: dict[str, Any]) -> Policy:
    with _lock:
        current = _current_policy()
        if "mode" in patch and patch["mode"] in {"readonly", "strict"}:
            current.mode = patch["mode"]
            if current.mode == "readonly":
                current.allow_writes = False
            elif "allow_writes" not in patch:
                current.allow_writes = True
        if "allow_writes" in patch:
            current.allow_writes = bool(patch["allow_writes"])
            if not current.allow_writes:
                current.mode = "readonly"
            elif current.mode == "readonly":
                current.mode = "strict"
        if "block_ddl" in patch:
            current.block_ddl = bool(patch["block_ddl"])
        if "require_where" in patch:
            current.require_where = bool(patch["require_where"])
        if "max_preview_rows" in patch:
            current.max_preview_rows = max(1, int(patch["max_preview_rows"]))
        if "require_typed_confirm_for" in patch:
            levels = patch["require_typed_confirm_for"]
            if isinstance(levels, list):
                current.require_typed_confirm_for = [
                    str(x).lower() for x in levels if str(x).lower() in {"low", "medium", "high"}
                ]
        if "auto_apply_low_risk" in patch:
            current.auto_apply_low_risk = bool(patch["auto_apply_low_risk"])
        if "auto_apply_max_rows" in patch:
            current.auto_apply_max_rows = max(1, int(patch["auto_apply_max_rows"]))
        return deepcopy(current)


class PolicyViolation(ValueError):
    """Raised when a write is refused before preview/commit."""


_WRITE_KIND = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE|ALTER|DROP|TRUNCATE|CREATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def is_write_sql(sql: str) -> bool:
    return bool(_WRITE_KIND.search(sql))


def should_auto_apply(risk_level: str, rows_affected: int | None) -> bool:
    policy = get_policy()
    if not policy.auto_apply_low_risk:
        return False
    if str(risk_level).lower() != "low":
        return False
    if rows_affected is None:
        return False
    return 0 <= rows_affected <= policy.auto_apply_max_rows


def enforce_write_proposal(sql: str) -> None:
    """Hard block before rehearsal. Safe default: refuse."""
    if not is_write_sql(sql):
        return

    policy = get_policy()
    stripped = sql.strip().rstrip(";")

    if policy.mode == "readonly" or not policy.allow_writes:
        raise PolicyViolation(
            "Policy is read-only. Enable writes in Safety before proposing changes."
        )

    if _MULTI_STATEMENT.search(sql.strip()):
        raise PolicyViolation("Multiple SQL statements are not allowed.")

    if policy.block_ddl and _DDL.search(stripped):
        raise PolicyViolation(
            "DDL is blocked by policy (ALTER/DROP/CREATE/TRUNCATE/…). "
            "Turn off 'Block DDL' in Safety only if you accept that risk."
        )

    if policy.require_where and _UPDATE_OR_DELETE.search(stripped):
        if not _HAS_WHERE.search(stripped):
            raise PolicyViolation(
                "UPDATE/DELETE without WHERE is blocked. Add a WHERE clause."
            )

    forbidden_schemas = {
        "sentinel",
        "information_schema",
        "crdb_internal",
        "pg_catalog",
        "pg_extension",
    }
    catalog_names: set[str] = set()
    catalog_tables: set[str] = set()
    try:
        from sentinel.connectors.catalog import allowed_objects

        for obj in allowed_objects():
            catalog_names.add(obj.lower())
            catalog_tables.add(obj.split(".")[-1].lower())
    except Exception:  # noqa: BLE001
        catalog_names = set()
        catalog_tables = set()

    if not catalog_names:
        raise PolicyViolation(
            "No tables in the connected catalog. Open Sources, connect or "
            "refresh the catalog, then retry."
        )

    for match in re.finditer(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)", stripped):
        schema = match.group(1).lower()
        table = match.group(2).lower()
        qualified = f"{schema}.{table}"
        if schema in forbidden_schemas:
            raise PolicyViolation(f"schema '{schema}' is off limits")
        if catalog_names and qualified not in catalog_names:
            raise PolicyViolation(
                f"{qualified} is not in the connected catalog allowlist."
            )

    if catalog_tables:
        for match in re.finditer(
            r"\b(?:UPDATE|INTO|FROM)\s+([A-Za-z_][\w]*)\b(?!\s*\.)",
            stripped,
            re.IGNORECASE,
        ):
            table = match.group(1).lower()
            if table in {"select", "only"}:
                continue
            if table not in catalog_tables:
                raise PolicyViolation(
                    f"table '{table}' is not in the connected catalog allowlist."
                )


def needs_typed_confirm(risk_level: str) -> bool:
    policy = get_policy()
    return risk_level.lower() in {x.lower() for x in policy.require_typed_confirm_for}


def check_typed_confirm(risk_level: str, confirmation: str | None) -> None:
    if not needs_typed_confirm(risk_level):
        return
    policy = get_policy()
    if (confirmation or "").strip() != policy.confirm_phrase:
        raise PolicyViolation(
            f"Risk is {risk_level}. Type {policy.confirm_phrase} to approve."
        )
