-- Daily-use food corrections: append-only supersession and void events.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_manual_food_consumption_entry_owner'
          AND conrelid = 'manual_food_consumption'::regclass
    ) THEN
        ALTER TABLE manual_food_consumption
            ADD CONSTRAINT uq_manual_food_consumption_entry_owner
            UNIQUE (entry_id, user_id);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS manual_food_consumption_adjustment (
    adjustment_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    client_event_id uuid NOT NULL,
    superseded_entry_id uuid NOT NULL,
    replacement_entry_id uuid NULL,
    kind text NOT NULL,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT fk_manual_adjustment_superseded_owner
        FOREIGN KEY (superseded_entry_id, user_id)
        REFERENCES manual_food_consumption(entry_id, user_id),
    CONSTRAINT fk_manual_adjustment_replacement_owner
        FOREIGN KEY (replacement_entry_id, user_id)
        REFERENCES manual_food_consumption(entry_id, user_id),
    CONSTRAINT uq_manual_adjustment_event UNIQUE (user_id, client_event_id),
    CONSTRAINT uq_manual_adjustment_superseded UNIQUE (user_id, superseded_entry_id),
    CONSTRAINT uq_manual_adjustment_replacement UNIQUE (user_id, replacement_entry_id),
    CONSTRAINT chk_manual_adjustment_kind CHECK (
        (kind = 'correction' AND replacement_entry_id IS NOT NULL
            AND replacement_entry_id <> superseded_entry_id)
        OR (kind = 'void' AND replacement_entry_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_manual_adjustment_owner_recorded
    ON manual_food_consumption_adjustment (user_id, recorded_at, adjustment_id);

ALTER TABLE manual_food_consumption_adjustment ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS manual_food_adjustment_owner_all
    ON manual_food_consumption_adjustment;
CREATE POLICY manual_food_adjustment_owner_all
    ON manual_food_consumption_adjustment FOR ALL TO authenticated
    USING (
        user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM manual_food_consumption original
            WHERE original.entry_id=manual_food_consumption_adjustment.superseded_entry_id
              AND original.user_id=NULLIF(
                  current_setting('request.jwt.claim.sub', true), ''
              )::uuid
        )
        AND (
            replacement_entry_id IS NULL
            OR EXISTS (
                SELECT 1 FROM manual_food_consumption replacement
                WHERE replacement.entry_id=manual_food_consumption_adjustment.replacement_entry_id
                  AND replacement.user_id=NULLIF(
                      current_setting('request.jwt.claim.sub', true), ''
                  )::uuid
            )
        )
    )
    WITH CHECK (
        user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM manual_food_consumption original
            WHERE original.entry_id=manual_food_consumption_adjustment.superseded_entry_id
              AND original.user_id=NULLIF(
                  current_setting('request.jwt.claim.sub', true), ''
              )::uuid
        )
        AND (
            replacement_entry_id IS NULL
            OR EXISTS (
                SELECT 1 FROM manual_food_consumption replacement
                WHERE replacement.entry_id=manual_food_consumption_adjustment.replacement_entry_id
                  AND replacement.user_id=NULLIF(
                      current_setting('request.jwt.claim.sub', true), ''
                  )::uuid
            )
        )
    );

REVOKE ALL ON manual_food_consumption_adjustment FROM authenticated;
GRANT SELECT, INSERT ON manual_food_consumption_adjustment TO authenticated;
REVOKE UPDATE, DELETE ON manual_food_consumption_adjustment FROM authenticated;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;

-- Rollback (only before any adjustment rows exist):
-- DROP TABLE manual_food_consumption_adjustment;
-- ALTER TABLE manual_food_consumption DROP CONSTRAINT uq_manual_food_consumption_entry_owner;
