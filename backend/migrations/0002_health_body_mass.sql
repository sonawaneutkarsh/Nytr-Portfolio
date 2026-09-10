-- Milestone 5 schema: HealthKit body-mass synchronization (ADR-016).
-- Scope: one durable body-mass history table with idempotent identity,
-- tombstone deletions (no DELETE anywhere), and Row Level Security keyed to
-- the Supabase JWT subject. Reversible via: DROP TABLE health_body_mass_sample;
-- (the DO blocks below are idempotent and safe to re-run).

-- The RLS policy targets the standard Supabase role. Create it when missing
-- (scratch/test databases) so the RLS proof test can run anywhere.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS health_body_mass_sample (
    id               uuid PRIMARY KEY,
    user_id          uuid NOT NULL,
    hk_sample_uuid   uuid NOT NULL,
    metric           text NOT NULL DEFAULT 'body_mass',
    value_kg         numeric(6,3),
    sample_start     timestamptz,
    sample_end       timestamptz,
    source_name      text,
    source_bundle_id text,
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    tombstoned_at    timestamptz,
    CONSTRAINT chk_hbm_metric CHECK (metric = 'body_mass'),
    CONSTRAINT chk_hbm_value_range CHECK (
        value_kg IS NULL OR (value_kg >= 20 AND value_kg <= 400)
    ),
    -- Measurement fields are jointly present or jointly absent; absence is
    -- reserved for tombstone-only rows (deletion seen before its sample).
    CONSTRAINT chk_hbm_measurement_shape CHECK (
        (value_kg IS NULL) = (sample_start IS NULL)
        AND (sample_start IS NULL) = (sample_end IS NULL)
    ),
    -- Active rows always carry the measurement; only tombstoned rows may be
    -- measurement-less.
    CONSTRAINT chk_hbm_active_has_value CHECK (
        tombstoned_at IS NOT NULL OR value_kg IS NOT NULL
    ),
    CONSTRAINT uq_hbm_user_sample UNIQUE (user_id, hk_sample_uuid)
);

CREATE INDEX IF NOT EXISTS ix_hbm_user_recent
    ON health_body_mass_sample (user_id, sample_start DESC);
CREATE INDEX IF NOT EXISTS ix_hbm_user_active
    ON health_body_mass_sample (user_id, sample_start DESC)
    WHERE tombstoned_at IS NULL;

ALTER TABLE health_body_mass_sample ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS hbm_owner_all ON health_body_mass_sample;
CREATE POLICY hbm_owner_all ON health_body_mass_sample
    FOR ALL TO authenticated
    USING (
        user_id
        = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    )
    WITH CHECK (
        user_id
        = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

GRANT SELECT, INSERT, UPDATE ON health_body_mass_sample TO authenticated;

-- Convenience for scratch databases where the DSN user is `postgres`: let it
-- assume the restricted role (SET ROLE) so requests run under RLS. Deployment
-- DSNs must hold equivalent membership in `authenticated`.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;
