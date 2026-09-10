-- Body & Goals: explicit profile data and append-only waist evidence.
-- Additive and backward-compatible.  HealthKit body mass and approved target
-- policy tables remain unchanged and authoritative for their own facts.

CREATE TABLE IF NOT EXISTS owner_body_profile (
    user_id          uuid PRIMARY KEY,
    height_cm        numeric(5,1) NOT NULL CHECK (height_cm >= 100 AND height_cm <= 250),
    target_weight_kg numeric(6,2) CHECK (target_weight_kg IS NULL OR (target_weight_kg >= 20 AND target_weight_kg <= 400)),
    updated_at       timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS waist_measurement (
    measurement_id uuid PRIMARY KEY,
    user_id        uuid NOT NULL,
    waist_cm       numeric(6,2) NOT NULL CHECK (waist_cm >= 30 AND waist_cm <= 250),
    measured_at    timestamptz NOT NULL,
    recorded_at    timestamptz NOT NULL,
    source         text NOT NULL DEFAULT 'owner_manual' CHECK (source = 'owner_manual')
);

CREATE INDEX IF NOT EXISTS ix_waist_measurement_user_recent
    ON waist_measurement (user_id, measured_at DESC, measurement_id DESC);

ALTER TABLE owner_body_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE waist_measurement ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS owner_body_profile_owner_all ON owner_body_profile;
CREATE POLICY owner_body_profile_owner_all ON owner_body_profile
    FOR ALL TO authenticated
    USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS waist_measurement_owner_all ON waist_measurement;
CREATE POLICY waist_measurement_owner_all ON waist_measurement
    FOR ALL TO authenticated
    USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE ON owner_body_profile TO authenticated;
GRANT SELECT, INSERT ON waist_measurement TO authenticated;
REVOKE DELETE ON owner_body_profile, waist_measurement FROM authenticated;
REVOKE UPDATE ON waist_measurement FROM authenticated;
