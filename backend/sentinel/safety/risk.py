from __future__ import annotations

import re
from dataclasses import dataclass, field

from sentinel.config import get_settings

_DDL = re.compile(
    r"\b(ALTER|DROP|TRUNCATE|CREATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|UPSERT|MERGE)\b", re.IGNORECASE)
_HAS_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_UPDATE_OR_DELETE = re.compile(r"\b(UPDATE|DELETE)\b", re.IGNORECASE)


@dataclass
class RiskAssessment:
    level: str  # low | medium | high
    kind: str  # read | write | ddl
    reasons: list[str] = field(default_factory=list)
    requires_backup: bool = False


def classify_risk(sql_text: str, rows_affected: int | None = None) -> RiskAssessment:
    """Deterministic risk classifier. No LLM judgment.

    High risk forces a ccloud backup before commit. Medium risk still
    requires the preview+approve path but skips the backup.
    """
    settings = get_settings()
    reasons: list[str] = []
    stripped = sql_text.strip().rstrip(";")

    if _DDL.search(stripped):
        return RiskAssessment(
            level="high",
            kind="ddl",
            reasons=["statement contains DDL"],
            requires_backup=True,
        )

    if not _WRITE.search(stripped):
        return RiskAssessment(level="low", kind="read", reasons=["read-only statement"])

    kind = "write"
    if _UPDATE_OR_DELETE.search(stripped) and not _HAS_WHERE.search(stripped):
        reasons.append("UPDATE/DELETE without a WHERE clause")

    if rows_affected is not None:
        if rows_affected >= settings.risk_rows_high:
            reasons.append(f"affects {rows_affected} rows (>= {settings.risk_rows_high})")
        elif rows_affected >= settings.risk_rows_medium:
            reasons.append(
                f"affects {rows_affected} rows (>= {settings.risk_rows_medium})"
            )

    if any("without a WHERE" in r for r in reasons) or (
        rows_affected is not None and rows_affected >= settings.risk_rows_high
    ):
        level = "high"
    elif reasons:
        level = "medium"
    else:
        level = "low"

    return RiskAssessment(
        level=level,
        kind=kind,
        reasons=reasons or ["bounded write"],
        requires_backup=level == "high",
    )
