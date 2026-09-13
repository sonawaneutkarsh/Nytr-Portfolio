-- Final completion milestone: Body & Goals evidence and starting-target lifecycle.
-- Additive, owner-scoped, append-only. No existing row is rewritten.

CREATE TABLE IF NOT EXISTS body_goal_profile_version (
    profile_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    policy_version text NOT NULL CHECK (policy_version = 'owner-body-goal-profile.v1'),
    height_cm numeric NOT NULL CHECK (height_cm BETWEEN 100 AND 250),
    date_of_birth date NOT NULL,
    formula_sex text NOT NULL CHECK (formula_sex IN ('male','female')),
    activity_level text NOT NULL CHECK (activity_level IN ('sedentary','lightly_active','moderately_active','very_active')),
    target_weight_kg numeric CHECK (target_weight_kg BETWEEN 20 AND 400),
    provenance text NOT NULL DEFAULT 'owner_entered' CHECK (provenance = 'owner_entered'),
    payload_sha256 text NOT NULL CHECK (length(payload_sha256) = 64),
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_body_goal_profile_owner_digest UNIQUE (user_id, payload_sha256),
    CONSTRAINT uq_body_goal_profile_owner_id UNIQUE (user_id, profile_id)
);
CREATE INDEX IF NOT EXISTS ix_body_goal_profile_owner_recent
    ON body_goal_profile_version (user_id, created_at DESC, profile_id DESC);

CREATE TABLE IF NOT EXISTS waist_measurement (
    measurement_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    measured_at timestamptz NOT NULL,
    value_cm numeric NOT NULL CHECK (value_cm BETWEEN 30 AND 250),
    entered_value numeric NOT NULL CHECK (entered_value > 0),
    entered_unit text NOT NULL CHECK (entered_unit IN ('cm','in')),
    provenance text NOT NULL CHECK (provenance = 'owner_entered'),
    corrects_measurement_id uuid,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT uq_waist_owner_id UNIQUE (user_id, measurement_id),
    CONSTRAINT fk_waist_owner_correction FOREIGN KEY (user_id, corrects_measurement_id)
        REFERENCES waist_measurement(user_id, measurement_id),
    CONSTRAINT uq_waist_single_correction UNIQUE (user_id, corrects_measurement_id),
    CONSTRAINT chk_waist_not_self_correction CHECK (corrects_measurement_id IS NULL OR corrects_measurement_id <> measurement_id)
);
CREATE INDEX IF NOT EXISTS ix_waist_owner_measured
    ON waist_measurement (user_id, measured_at DESC, measurement_id DESC);

CREATE TABLE IF NOT EXISTS starting_calorie_proposal (
    proposal_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    goal_policy_version_id uuid NOT NULL,
    body_mass_sample_uuid uuid NOT NULL,
    policy_version text NOT NULL CHECK (policy_version = 'mifflin-st-jeor-starting-target.v1'),
    as_of_date date NOT NULL,
    timezone text NOT NULL CHECK (btrim(timezone) <> ''),
    age_years integer NOT NULL CHECK (age_years BETWEEN 18 AND 120),
    body_mass_kg numeric NOT NULL CHECK (body_mass_kg BETWEEN 20 AND 400),
    bmr_kcal numeric NOT NULL CHECK (bmr_kcal > 0),
    activity_multiplier numeric NOT NULL CHECK (activity_multiplier IN (1.2,1.375,1.55,1.725)),
    maintenance_kcal numeric NOT NULL CHECK (maintenance_kcal > 0),
    goal_adjustment_kcal numeric NOT NULL CHECK (goal_adjustment_kcal IN (200,0,-300)),
    proposed_calorie_kcal numeric NOT NULL CHECK (proposed_calorie_kcal > 0 AND mod(proposed_calorie_kcal,50)=0),
    evidence_sha256 text NOT NULL CHECK (length(evidence_sha256) = 64),
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_starting_proposal_owner_digest UNIQUE (user_id, evidence_sha256),
    CONSTRAINT uq_starting_proposal_owner_id UNIQUE (user_id, proposal_id),
    CONSTRAINT fk_starting_profile_owner FOREIGN KEY (user_id, profile_id)
        REFERENCES body_goal_profile_version(user_id, profile_id),
    CONSTRAINT fk_starting_goal FOREIGN KEY (goal_policy_version_id)
        REFERENCES goal_policy_version(version_id),
    CONSTRAINT fk_starting_weight_owner FOREIGN KEY (user_id, body_mass_sample_uuid)
        REFERENCES health_body_mass_sample(user_id, hk_sample_uuid)
);
CREATE INDEX IF NOT EXISTS ix_starting_proposal_owner_recent
    ON starting_calorie_proposal (user_id, created_at DESC, proposal_id DESC);

CREATE TABLE IF NOT EXISTS starting_calorie_proposal_decision (
    decision_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    proposal_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved','rejected')),
    client_event_id uuid NOT NULL,
    resulting_target_policy_version_id uuid,
    decided_at timestamptz NOT NULL,
    CONSTRAINT fk_starting_decision_owner FOREIGN KEY (user_id, proposal_id)
        REFERENCES starting_calorie_proposal(user_id, proposal_id),
    CONSTRAINT uq_starting_decision_terminal UNIQUE (user_id, proposal_id),
    CONSTRAINT uq_starting_decision_event UNIQUE (user_id, client_event_id),
    CONSTRAINT chk_starting_decision_result CHECK (
        (decision='approved' AND resulting_target_policy_version_id IS NOT NULL)
        OR (decision='rejected' AND resulting_target_policy_version_id IS NULL)
    ),
    CONSTRAINT fk_starting_result FOREIGN KEY (resulting_target_policy_version_id)
        REFERENCES target_policy_version(version_id)
);

ALTER TABLE body_goal_profile_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE waist_measurement ENABLE ROW LEVEL SECURITY;
ALTER TABLE starting_calorie_proposal ENABLE ROW LEVEL SECURITY;
ALTER TABLE starting_calorie_proposal_decision ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS body_goal_profile_owner_all ON body_goal_profile_version;
CREATE POLICY body_goal_profile_owner_all ON body_goal_profile_version FOR ALL TO authenticated
USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
WITH CHECK (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS waist_measurement_owner_all ON waist_measurement;
CREATE POLICY waist_measurement_owner_all ON waist_measurement FOR ALL TO authenticated
USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
WITH CHECK (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS starting_calorie_proposal_owner_all ON starting_calorie_proposal;
CREATE POLICY starting_calorie_proposal_owner_all ON starting_calorie_proposal FOR ALL TO authenticated
USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
WITH CHECK (
    user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    AND EXISTS (SELECT 1 FROM body_goal_profile_version p WHERE p.profile_id=starting_calorie_proposal.profile_id AND p.user_id=starting_calorie_proposal.user_id)
    AND EXISTS (SELECT 1 FROM goal_policy_version g WHERE g.version_id=starting_calorie_proposal.goal_policy_version_id AND g.user_id=starting_calorie_proposal.user_id)
    AND EXISTS (SELECT 1 FROM health_body_mass_sample h WHERE h.hk_sample_uuid=starting_calorie_proposal.body_mass_sample_uuid AND h.user_id=starting_calorie_proposal.user_id AND h.tombstoned_at IS NULL)
);

DROP POLICY IF EXISTS starting_calorie_decision_owner_all ON starting_calorie_proposal_decision;
CREATE POLICY starting_calorie_decision_owner_all ON starting_calorie_proposal_decision FOR ALL TO authenticated
USING (user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
WITH CHECK (
    user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    AND EXISTS (SELECT 1 FROM starting_calorie_proposal p WHERE p.proposal_id=starting_calorie_proposal_decision.proposal_id AND p.user_id=starting_calorie_proposal_decision.user_id)
    AND (resulting_target_policy_version_id IS NULL OR EXISTS (
        SELECT 1 FROM target_policy_version t WHERE t.version_id=starting_calorie_proposal_decision.resulting_target_policy_version_id AND t.user_id=starting_calorie_proposal_decision.user_id
    ))
);

REVOKE UPDATE, DELETE ON TABLE body_goal_profile_version, waist_measurement,
    starting_calorie_proposal, starting_calorie_proposal_decision FROM authenticated;
GRANT SELECT, INSERT ON TABLE body_goal_profile_version, waist_measurement,
    starting_calorie_proposal, starting_calorie_proposal_decision TO authenticated;
