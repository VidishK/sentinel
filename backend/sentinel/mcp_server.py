"""Sentinel MCP server — read-only interrogation of agentic memory.

Run:
    .venv/bin/python -m sentinel.mcp_server

Tools expose the same Cockroach VECTOR memory the chat agent uses:
trusted rules, schema docs, and the action/audit trail.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from sentinel.memory.actions import search_similar_actions
from sentinel.memory.rules import list_rules, search_rules, search_trusted_rules
from sentinel.memory.schema_memory import search_schema_docs
from sentinel.safety.policy import get_policy

mcp = FastMCP(
    "sentinel-memory",
    instructions=(
        "Read-only access to Sentinel's CockroachDB memory layer: "
        "trusted rules, schema embeddings, past actions, and the live safety policy."
    ),
)


@mcp.tool()
def search_trusted_company_rules(query: str, limit: int = 5) -> str:
    """Semantic search over trusted procedural rules (Titan + VECTOR)."""
    rows = search_trusted_rules(query, limit=limit)
    return json.dumps(rows, default=str)


@mcp.tool()
def search_all_rules(query: str, status: str | None = None, limit: int = 8) -> str:
    """Vector search across the rule library, optionally filtered by status."""
    rows = search_rules(query, limit=limit, status=status)
    return json.dumps(rows, default=str)


@mcp.tool()
def list_company_rules(status: str | None = None) -> str:
    """List stored rules. Status is probation, trusted, rejected, or retired."""
    rows = list_rules(status)
    return json.dumps(rows, default=str)


@mcp.tool()
def search_schema_memory(query: str, limit: int = 4) -> str:
    """Semantic retrieval over embedded company schema docs."""
    rows = search_schema_docs(query, limit=limit)
    return json.dumps(rows, default=str)


@mcp.tool()
def search_past_actions(query: str, limit: int = 5) -> str:
    """Semantic search over past previewed/committed writes."""
    rows = search_similar_actions(query, limit=limit)
    return json.dumps(rows, default=str)


@mcp.tool()
def get_safety_policy() -> str:
    """Return the live write policy (readonly vs strict, DDL, WHERE, row caps)."""
    return json.dumps(get_policy().to_dict(), default=str)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
