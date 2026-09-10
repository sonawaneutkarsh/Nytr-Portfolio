-- Milestone 6 schema: daily-plan persistence, target policies, decision log.
-- (ADR-017/018; docs/DATABASE_SCHEMA.md §plan-persistence)
--
-- Conventions follow migrations 0001/0002: uuid PKs, IF NOT EXISTS idempotency,
-- CHECK-constrained enums, no DELETE anywhere, RLS owner policy keyed to the
-- Supabase JWT subject (`request.jwt.claim.sub`), and scratch-DB role bootstrap.
-- Reversible via:
--   DROP TABLE plan_item; DROP TABLE plan_version; DROP TABLE plan_run;
--   DROP TABLE decision_log; DROP TABLE target_policy_version;

-- Role bootstrap mirrors 0002 so the RLS proof tests run on scratch databases.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Target policy versions (ADR-018): immutable approved goal sets per user.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS target_policy_version (
    version_id      uuid PRIMARY KEY,
    user_id         uuid NOT NULL,
    policy_version  text NOT NULL,
    -- Normalized goals: [{nutrient, kind, value(str), weight(str)}]; values are
    -- decimal strings (ADR-016 wire discipline); shapes validated in domain.
    goals           jsonb NOT NULL,
    payload_sha256  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_tpv_user_version UNIQUE (user_id, policy_version),
    CONSTRAINT uq_tpv_user_payload UNIQUE (user_id, payload_sha256)
);
CREATE INDEX IF NOT EXISTS ix_tpv_user_recent
    ON target_policy_version (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS decision_log (
    decision_id       uuid PRIMARY KEY,
    user_id           uuid NOT NULL,
    subject           text NOT NULL CHECK (subject IN ('target_policy')),
    decision          text NOT NULL CHECK (decision IN ('approved')),
    rationale         text NOT NULL,
    policy_version_id uuid NOT NULL REFERENCES target_policy_version(version_id),
    decided_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_decision_user_subject
    ON decision_log (user_id, subject, decided_at DESC);

-- ---------------------------------------------------------------------------
-- Daily plans (ADR-017): execution spine, immutable artifact, queryable items.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plan_run (
    run_id               uuid PRIMARY KEY,
    user_id              uuid NOT NULL,
    requested_for_date   date NOT NULL,
    timezone             text NOT NULL,
    inputs_fingerprint   text NOT NULL,
    status               text NOT NULL CHECK (status IN ('completed','no_plan')),
    reason_codes         text[] NOT NULL DEFAULT '{}',
    started_at           timestamptz NOT NULL,
    finished_at          timestamptz NOT NULL,
    -- Idempotency key: identical resolved inputs can yield only one logical run.
    CONSTRAINT uq_plan_run_inputs UNIQUE (user_id, requested_for_date, inputs_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_plan_run_user_date
    ON plan_run (user_id, requested_for_date);

CREATE TABLE IF NOT EXISTS plan_version (
    version_id     uuid PRIMARY KEY,
    run_id         uuid NOT NULL REFERENCES plan_run(run_id),
    -- Structurally identical projection of the canonical artifact (debug/query).
    plan_jsonb     jsonb NOT NULL,
    -- Exact byte-stable UTF-8 artifact; SHA-256 over THESE bytes is plan_sha256.
    plan_canonical text NOT NULL,
    plan_sha256    text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, plan_sha256)
);
CREATE INDEX IF NOT EXISTS ix_plan_version_run ON plan_version (run_id);

CREATE TABLE IF NOT EXISTS plan_item (
    item_id          uuid PRIMARY KEY,
    version_id       uuid NOT NULL REFERENCES plan_version(version_id),
    slot_index       int NOT NULL,
    context          text NOT NULL,
    rank             int NOT NULL,
    candidate_id     text NOT NULL,
    menu_period      text NOT NULL,
    -- Provenance pins copied AT PLAN TIME. menu_offering rows are upsertable;
    -- nutrition_profile rows are immutable, so profile pinning is permanent.
    food_ids         uuid[] NOT NULL,
    offering_ids     uuid[] NOT NULL,
    profile_ids      uuid[] NOT NULL,
    score_total      text NOT NULL,
    calories_kcal    text NOT NULL,
    CONSTRAINT uq_plan_item_rank UNIQUE (version_id, slot_index, rank)
);
CREATE INDEX IF NOT EXISTS ix_plan_item_version ON plan_item (version_id, slot_index, rank);

ALTER TABLE target_policy_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_item ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tpv_owner_all ON target_policy_version;
CREATE POLICY tpv_owner_all ON target_policy_version
    FOR ALL TO authenticated
    USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS decision_owner_all ON decision_log;
-- Ownership flows through the parent policy version: the subject must own
-- BOTH the decision row and the target_policy_version it references, so a
-- user cannot attach an "approval" to someone else's policy.
CREATE POLICY decision_owner_all ON decision_log
    FOR ALL TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM target_policy_version p
            WHERE p.version_id = decision_log.policy_version_id
              AND p.user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    )
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM target_policy_version p
            WHERE p.version_id = decision_log.policy_version_id
              AND p.user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS plan_run_owner_all ON plan_run;
CREATE POLICY plan_run_owner_all ON plan_run
    FOR ALL TO authenticated
    USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS plan_version_owner_all ON plan_version;
CREATE POLICY plan_version_owner_all ON plan_version
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM plan_run r
            WHERE r.run_id = plan_version.run_id
              AND r.user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM plan_run r
            WHERE r.run_id = plan_version.run_id
              AND r.user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS plan_item_owner_all ON plan_item;
CREATE POLICY plan_item_owner_all ON plan_item
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM plan_version v JOIN plan_run r ON r.run_id = v.run_id
            WHERE v.version_id = plan_item.version_id
              AND r.user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM plan_version v JOIN plan_run r ON r.run_id = v.run_id
            WHERE v.version_id = plan_item.version_id
              AND r.user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

-- M6 records are immutable after insertion (ADR-017/018). Revoke explicitly
-- so reapplying this still-uncommitted migration also corrects a scratch DB
-- that received an earlier draft which granted UPDATE.
REVOKE UPDATE, DELETE ON TABLE
    target_policy_version, decision_log, plan_run, plan_version, plan_item
    FROM authenticated;
GRANT SELECT, INSERT ON TABLE
    target_policy_version, decision_log, plan_run, plan_version, plan_item
    TO authenticated;

-- Scratch-DB convenience identical to 0002.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;
