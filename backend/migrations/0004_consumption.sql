-- Milestone 7 Step 2: immutable plan consumption + historical target-policy pin.
-- Scope is database-only: no repository/API behavior is introduced here.
-- Reversible via: DROP TABLE plan_consumption;

-- Historical M6 runs predate this reference, so NULL remains valid. The
-- constraint is added separately so reapplying the migration is idempotent.
ALTER TABLE plan_run
    ADD COLUMN IF NOT EXISTS target_policy_version_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_plan_run_target_policy_version'
          AND conrelid = 'plan_run'::regclass
    ) THEN
        ALTER TABLE plan_run
            ADD CONSTRAINT fk_plan_run_target_policy_version
            FOREIGN KEY (target_policy_version_id)
            REFERENCES target_policy_version(version_id);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS plan_consumption (
    entry_id                uuid PRIMARY KEY,
    user_id                 uuid NOT NULL,
    plan_run_id             uuid NOT NULL REFERENCES plan_run(run_id),
    plan_version_id         uuid NOT NULL REFERENCES plan_version(version_id),
    item_id                 uuid NOT NULL REFERENCES plan_item(item_id),
    state                   text NOT NULL,
    client_event_id         uuid NOT NULL,
    recorded_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_plan_consumption_state CHECK (
        state IN ('eaten','skipped','unavailable','alternative')
    ),
    CONSTRAINT uq_plan_consumption_event UNIQUE (
        user_id, plan_version_id, item_id, client_event_id
    )
);

CREATE INDEX IF NOT EXISTS ix_plan_consumption_user_run
    ON plan_consumption (user_id, plan_run_id);

ALTER TABLE plan_consumption ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS plan_consumption_owner_all ON plan_consumption;
CREATE POLICY plan_consumption_owner_all ON plan_consumption
    FOR ALL TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1
            FROM plan_run r
            JOIN plan_version v ON v.run_id = r.run_id
            JOIN plan_item i ON i.version_id = v.version_id
            WHERE r.run_id = plan_consumption.plan_run_id
              AND v.version_id = plan_consumption.plan_version_id
              AND i.item_id = plan_consumption.item_id
              AND r.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    )
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1
            FROM plan_run r
            JOIN plan_version v ON v.run_id = r.run_id
            JOIN plan_item i ON i.version_id = v.version_id
            WHERE r.run_id = plan_consumption.plan_run_id
              AND v.version_id = plan_consumption.plan_version_id
              AND i.item_id = plan_consumption.item_id
              AND r.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

-- Consumption entries are immutable: exact retries are resolved by the later
-- repository layer through the uniqueness key, never by UPDATE or DELETE.
REVOKE UPDATE, DELETE ON TABLE plan_consumption FROM authenticated;
GRANT SELECT, INSERT ON TABLE plan_consumption TO authenticated;
