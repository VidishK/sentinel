-- Sentinel control plane.
--
-- Everything Sentinel knows about itself lives in the `sentinel` schema.
-- The database the agent operates on lives in `company` (see 002_company_seed.sql).
-- The agent is only ever granted access to `company`; it cannot rewrite its own
-- memory or audit trail through the SQL it generates.

CREATE SCHEMA IF NOT EXISTS sentinel;

-- ---------------------------------------------------------------------------
-- Conversation
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sentinel.sessions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title      STRING,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sentinel.messages (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sentinel.sessions (id),
    role       STRING NOT NULL,
    content    STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT valid_role CHECK (role IN ('user', 'assistant', 'system')),
    INDEX messages_by_session (session_id, created_at)
);

-- ---------------------------------------------------------------------------
-- Procedural memory: rules the agent is allowed to follow
--
-- A rule is never applied while in 'probation'. It only reaches 'trusted' by
-- winning a measured A/B comparison, recorded in rule_trials + rule_decisions.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sentinel.rules (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    body              STRING NOT NULL,
    trigger_text      STRING NOT NULL,
    status            STRING NOT NULL DEFAULT 'probation',
    source_message_id UUID REFERENCES sentinel.messages (id),
    embedding         VECTOR(1024),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT valid_status CHECK (
        status IN ('probation', 'trusted', 'rejected', 'retired')
    ),
    INDEX rules_by_status (status, created_at DESC),
    VECTOR INDEX rules_trigger_idx (embedding vector_cosine_ops)
);

-- ---------------------------------------------------------------------------
-- Evaluation set used to judge rules
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sentinel.tasks (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family   STRING NOT NULL,
    kind     STRING NOT NULL,
    prompt   STRING NOT NULL,
    -- For read tasks: the expected answer. For write tasks: a SQL predicate
    -- evaluated after the change that must return TRUE.
    expected JSONB,
    checker  STRING,
    CONSTRAINT valid_kind CHECK (kind IN ('read', 'write')),
    INDEX tasks_by_family (family)
);

CREATE TABLE IF NOT EXISTS sentinel.rule_trials (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id    UUID NOT NULL REFERENCES sentinel.rules (id),
    task_id    UUID NOT NULL REFERENCES sentinel.tasks (id),
    batch_id   UUID NOT NULL,
    with_rule  BOOL NOT NULL,
    passed     BOOL NOT NULL,
    tokens     INT,
    latency_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX trials_by_rule (rule_id, with_rule)
);

-- Every status change carries the evidence that justified it. Written in the
-- same transaction that flips rules.status, so a promotion can never exist
-- without the numbers behind it.
CREATE TABLE IF NOT EXISTS sentinel.rule_decisions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id      UUID NOT NULL REFERENCES sentinel.rules (id),
    batch_id     UUID,
    from_status  STRING NOT NULL,
    to_status    STRING NOT NULL,
    n_with       INT NOT NULL,
    n_without    INT NOT NULL,
    pass_with    INT NOT NULL,
    pass_without INT NOT NULL,
    delta        FLOAT8 NOT NULL,
    ci_low       FLOAT8 NOT NULL,
    ci_high      FLOAT8 NOT NULL,
    decided_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX decisions_by_rule (rule_id, decided_at DESC)
);

-- ---------------------------------------------------------------------------
-- The write path
--
-- Every statement the agent wants to run against `company` becomes a row here
-- first, in status 'previewed', with the diff captured from a transaction that
-- was rolled back. Nothing reaches 'committed' without explicit approval.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sentinel.actions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID REFERENCES sentinel.sessions (id),
    request          STRING NOT NULL,
    sql_text         STRING NOT NULL,
    kind             STRING NOT NULL,
    risk             STRING NOT NULL,
    risk_reasons     JSONB,
    rows_affected    INT,
    diff_s3_key      STRING,
    diff_summary     JSONB,
    status           STRING NOT NULL DEFAULT 'previewed',
    backup_id        STRING,
    intent_embedding VECTOR(1024),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at     TIMESTAMPTZ,
    CONSTRAINT valid_kind CHECK (kind IN ('read', 'write', 'ddl')),
    CONSTRAINT valid_risk CHECK (risk IN ('low', 'medium', 'high')),
    CONSTRAINT valid_status CHECK (
        status IN ('previewed', 'approved', 'committed', 'rejected', 'failed')
    ),
    INDEX actions_recent (created_at DESC),
    VECTOR INDEX actions_intent_idx (intent_embedding vector_cosine_ops)
);

-- Which trusted rules were in the agent's context when it produced this action.
CREATE TABLE IF NOT EXISTS sentinel.action_rules (
    action_id UUID NOT NULL REFERENCES sentinel.actions (id),
    rule_id   UUID NOT NULL REFERENCES sentinel.rules (id),
    PRIMARY KEY (action_id, rule_id)
);

CREATE TABLE IF NOT EXISTS sentinel.audit_events (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id  UUID REFERENCES sentinel.actions (id),
    event_type STRING NOT NULL,
    actor      STRING NOT NULL DEFAULT 'agent',
    payload    JSONB,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX audit_by_action (action_id, at),
    INDEX audit_recent (at DESC)
);
