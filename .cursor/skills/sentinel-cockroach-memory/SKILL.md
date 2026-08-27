---
name: sentinel-cockroach-memory
description: >-
  Use when writing SQL against CockroachDB for Sentinel, proposing writes,
  retrieving agent memory, or explaining preview/rollback/vector search.
---

# Sentinel + CockroachDB memory

Sentinel is an agent that may propose writes to the **active data source**
(demo `company.*`, or a connected Postgres/Cockroach/Mongo catalog). It never
commits unseen. Follow this contract.

## Schema

Memory (`sentinel.*`) always stays on the control-plane Cockroach cluster.
Operational SQL runs against the active source.

Call `list_tables` first. Qualify tables as returned (`public.products`,
`company.customers`, …). Never touch `sentinel.*` or system catalogs.

On the shared demo, operate on `company` tables: customers, products, prices,
price_history, orders, order_items, refunds. Exclude `is_test = true` unless
the user asked about test accounts. Subtract refunds from revenue.

## Writes

1. Rehearse with `propose_write` (SQL) or `mongo_propose_write` (Mongo).
   Never run INSERT/UPDATE/DELETE yourself.
2. UPDATE/DELETE must include WHERE. DDL is blocked unless Safety allows it.
3. If the write auto-commits, say it is applied. If it waits, say it is in Preview.
   Never mention action IDs. Never give numbered click-by-click instructions.
4. High-risk commits record a ccloud backup id plus
   `cluster_logical_timestamp()` for AS OF SYSTEM TIME undo.

## Memory

Rules start in probation. Only `trusted` rules are followed. Retrieval is
semantic: Titan embeddings + CockroachDB `VECTOR(1024)` cosine indexes over
rules, schema docs, and action intents.

If the user states a durable convention, save it with `propose_rule`.
