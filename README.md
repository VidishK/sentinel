# Sentinel

**Most AI database tools ask you to trust them. Sentinel proves itself before it touches anything.**

Sentinel is an AI agent you can safely give write access to a company database. Every
change it proposes is rehearsed inside a transaction and rolled back so you can see the
exact rows it would touch before anything is committed. Risky operations take a real
cluster backup first. And the agent only follows company rules that have measurably
improved its accuracy on a held-out evaluation set.

Built for the CockroachDB x AWS Hackathon: Build with Agentic Memory.

## Why this exists

Text-to-SQL assistants are everywhere, and nearly all of them are read-only, because
handing an AI write access to production is terrifying. The interesting problem is not
"can the model write SQL" but "under what contract is it allowed to run it."

There is a second, quieter failure. Agents that write their own how-to notes get *worse*
on average: SkillsBench (arXiv:2602.12670, 7,308 trajectories) found human-curated skills
raise agent pass rates by 16.2 points while self-generated skills come in at -1.3. Nothing
validates a note before the agent starts relying on it. Sentinel's rule library is that
missing gate.

## The three guarantees

1. **Nothing commits unseen.** Proposed writes execute inside a transaction, the affected
   rows are captured before and after, then the transaction is rolled back and the diff is
   shown. The statement only runs for real after explicit approval.
2. **Nothing risky happens without an undo.** A deterministic risk classifier (DDL, missing
   `WHERE`, row count over threshold) forces a `ccloud` backup before commit.
3. **No unproven rule is ever followed.** New rules enter `probation` and are only promoted
   to `trusted` after an A/B comparison across the evaluation set clears a Wilson score
   interval. Rules that stop helping are demoted automatically.

## Architecture

```
User -> FastAPI (Lambda) -> Bedrock Claude
                |
                +-- vector search over rules, past actions, schema  --> CockroachDB
                +-- deterministic risk classifier (Lambda)
                +-- ccloud backup guard
                +-- transaction preview / rollback / commit
                +-- audit trail                                      --> CockroachDB
```

CockroachDB holds the operational data, the rule library, the embeddings (`VECTOR(1024)`
columns with cosine vector indexes), the trial evidence, and the audit trail in one
database. Its transactions are what make the preview possible at all.

## Layout

```
backend/sql/001_sentinel_schema.sql   control plane: rules, trials, actions, audit
backend/sql/002_company_seed.sql      the demo company database the agent operates on
backend/sentinel/                     FastAPI app: agent, memory, safety, api
scripts/validate_cluster.py           day-0 checks: vector index, rollback, time travel
frontend/                             chat plus the live memory panel
```

## Setup

Requires Python 3.12+, Node 20+, the `ccloud` CLI, and an AWS account with Bedrock model
access for Claude and Titan Text Embeddings V2.

```bash
# 1. Cluster
ccloud auth login
ccloud cluster create basic sentinel us-east-1 --cloud AWS

# 2. Environment
cp .env.example .env        # fill in DATABASE_URL and ARTIFACT_BUCKET
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 3. Verify the cluster does what the design assumes
.venv/bin/python scripts/validate_cluster.py

# 4. Schema and seed data
.venv/bin/python scripts/apply_sql.py backend/sql/001_sentinel_schema.sql
.venv/bin/python scripts/apply_sql.py backend/sql/002_company_seed.sql
```

## CockroachDB tools used

- **Distributed vector indexing** - cosine indexes over rule triggers and action intents,
  powering rule retrieval, duplicate/contradiction detection, and semantic audit search.
- **Managed MCP Server** - a read-only view over the rule library and audit trail, so the
  memory can be interrogated directly from Claude or Cursor.
- **ccloud CLI** - cluster provisioning, and the backup taken before every high-risk action.

## AWS services used

- **Amazon Bedrock** - Claude for reasoning and SQL generation, Titan Text Embeddings V2
  for the 1024-dimension vectors.
- **AWS Lambda** - the API, the deterministic risk classifier, and parallel rule trials.
- **Amazon S3** - change diffs, trial artifacts, and static frontend hosting.

## License

MIT
