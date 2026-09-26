# Sentinel

**Most AI database tools ask you to trust them. Sentinel proves itself before it touches anything.**

An AI agent you can give write access to a company database. Every proposed
write is rehearsed in a transaction, the before/after rows are captured, and
the transaction is rolled back until a human approves. High-risk commits
record a Cockroach Cloud backup plus a `cluster_logical_timestamp()`. Rules
the agent follows must earn `trusted` status on a held-out A/B eval.


## What you get

- Chat agent (Amazon Nova Pro on Bedrock) scoped to the **active source**
  (shared demo `company.*`, or a Postgres / Cockroach / Mongo you connect)
- Sign-in, per-account sources, and a Sources tab to paste a connection string
- Preview → Approve / Reject, with typed `COMMIT` on medium/high risk
- Strict policy: catalog allowlist, DDL block, WHERE required, read-only mode
- Vector memory: Titan embeddings + Cockroach `VECTOR(1024)` cosine indexes
  over rules, schema docs, and past action intents
- Rule library: probation → A/B trials → Wilson-gated promotion
- ccloud backup listing recorded on high-risk commits
- MCP server so Cursor/Claude can interrogate the same memory layer


Required: **≥2 CockroachDB tools** and **≥1 AWS service**, used for real.

| Requirement | What Sentinel actually does |
|-------------|-----------------------------|
| **Distributed vector indexing** | `VECTOR(1024)` + cosine indexes on `sentinel.rules`, `sentinel.actions.intent_embedding`, and `sentinel.schema_docs`. Every chat turn retrieves trusted rules, schema blurbs, and similar past writes. The Rules tab runs the same search. |
| **ccloud CLI (agent-ready)** | Cluster `sentinel` was provisioned with ccloud. High-risk approve calls `ccloud cluster backup list` and stores the latest backup id + `cluster_logical_timestamp()` on the action. Safety shows recent undo points. |
| **Managed MCP Server** | Cockroach Cloud MCP can be added from the Cloud Console (read-only SQL over the cluster). Sentinel also ships `python -m sentinel.mcp_server` for rule/schema/action retrieval. |
| **Agent Skills** | Project skill at `.cursor/skills/sentinel-cockroach-memory/SKILL.md` encodes the preview/allowlist/memory contract. |
| **Amazon Bedrock** | Nova Pro (`amazon.nova-pro-v1:0`) for chat + SQL; Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) for 1024-dim vectors. |

## Architecture

```
User (Vite UI)
    → FastAPI
        → Bedrock Nova Pro  (reason + tool calls)
        → Titan embeddings  (rules / schema / action intents)
        → CockroachDB Cloud `defaultdb`
              company.*     demo operational data
              sentinel.*    sessions, rules, trials, actions, audit, vectors
        → ccloud backup list  (high-risk restore point)
```

## Layout

```
backend/sql/001_sentinel_schema.sql   control plane + VECTOR indexes
backend/sql/002_company_seed.sql      demo company database
backend/sql/003_eval_tasks.sql        A/B tasks for rule promotion
backend/sentinel/                     FastAPI app
  agent/     Bedrock loop + tools
  memory/    rules, trials, schema docs, action embeddings
  safety/    preview, risk, policy, backup guard
  mcp_server.py
frontend/                              chat + live memory
scripts/validate_cluster.py            vector index, rollback, time travel
.cursor/skills/                        agent skill
.cursor/mcp.json.example               Sentinel MCP snippet
```

## Setup

Requires Python 3.12+, Node 20+, the `ccloud` CLI, and AWS Bedrock access
for Nova Pro and Titan Text Embeddings V2.

```bash
# 1. Cluster
ccloud auth login
ccloud cluster create basic sentinel us-east-1 --cloud AWS

# 2. Environment
cp .env.example .env        # DATABASE_URL, AWS region, cluster name
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 3. Day-0 checks (vector index, rollback, AS OF SYSTEM TIME)
.venv/bin/python scripts/validate_cluster.py

# 4. Schema and seed
.venv/bin/python scripts/apply_sql.py backend/sql/001_sentinel_schema.sql
.venv/bin/python scripts/apply_sql.py backend/sql/002_company_seed.sql
.venv/bin/python scripts/apply_sql.py backend/sql/003_eval_tasks.sql
```

## Run locally

```bash
# API
cd backend && ../.venv/bin/uvicorn sentinel.main:app --host 127.0.0.1 --port 8001 --reload

# UI (proxies /api → 8001)
cd frontend && npm install && npm run dev
```

Open http://127.0.0.1:5173

Sign up, then open **Sources**. Paste a `postgresql://…` or `mongodb://…`
string for a database this machine can reach. Memory stays on Cockroach
(`sentinel.*`); the agent reads and rehearses writes against the source you
activated. The shared demo is playground data.

Optional MCP (from repo root, after copying `.cursor/mcp.json.example` to `.cursor/mcp.json`):

```bash
cd backend && ../.venv/bin/python -m sentinel.mcp_server
```

## License

MIT
