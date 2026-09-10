-- M16A: reviewed protein-target proposals and immutable next-meal artifacts.
-- Recommendations are not consumption records. Every row is owner-scoped,
-- append-only, and pins the exact evidence used when it was generated.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_target_policy_user_version_id'
          AND conrelid = 'target_policy_version'::regclass
    ) THEN
        ALTER TABLE target_policy_version
            ADD CONSTRAINT uq_target_policy_user_version_id UNIQUE (user_id, version_id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS protein_target_proposal (
    proposal_id                    uuid PRIMARY KEY,
    user_id                        uuid NOT NULL,
    prior_target_policy_version_id uuid NOT NULL,
    body_mass_sample_uuid          uuid NOT NULL,
    policy_version                 text NOT NULL,
    target_kind                    text NOT NULL CHECK (target_kind IN ('target','floor')),
    body_mass_kg                   numeric NOT NULL CHECK (
        body_mass_kg > 0 AND body_mass_kg NOT IN ('NaN'::numeric,'Infinity'::numeric)
    ),
    grams_per_pound                numeric NOT NULL CHECK (
        grams_per_pound > 0 AND grams_per_pound NOT IN ('NaN'::numeric,'Infinity'::numeric)
    ),
    proposed_protein_g             numeric NOT NULL CHECK (
        proposed_protein_g > 0
        AND proposed_protein_g NOT IN ('NaN'::numeric,'Infinity'::numeric)
    ),
    evidence_digest                text NOT NULL CHECK (evidence_digest ~ '^[0-9a-f]{64}$'),
    calculation_payload            jsonb NOT NULL,
    rationale                      text NOT NULL CHECK (btrim(rationale) <> ''),
    provenance                     text NOT NULL CHECK (btrim(provenance) <> ''),
    generated_at                   timestamptz NOT NULL,
    CONSTRAINT fk_protein_proposal_target_owner FOREIGN KEY
        (user_id, prior_target_policy_version_id)
        REFERENCES target_policy_version(user_id, version_id),
    CONSTRAINT fk_protein_proposal_body_mass_owner FOREIGN KEY
        (user_id, body_mass_sample_uuid)
        REFERENCES health_body_mass_sample(user_id, hk_sample_uuid),
    CONSTRAINT uq_protein_proposal_user_digest UNIQUE (user_id, evidence_digest),
    CONSTRAINT uq_protein_proposal_user_id UNIQUE (user_id, proposal_id)
);

CREATE INDEX IF NOT EXISTS ix_protein_proposal_user_recent
    ON protein_target_proposal (user_id, generated_at DESC, proposal_id DESC);

CREATE TABLE IF NOT EXISTS protein_target_proposal_decision (
    decision_id                        uuid PRIMARY KEY,
    user_id                            uuid NOT NULL,
    proposal_id                        uuid NOT NULL,
    decision                           text NOT NULL CHECK (decision IN ('approved','rejected')),
    rationale                          text NOT NULL CHECK (btrim(rationale) <> ''),
    resulting_target_policy_version_id uuid,
    client_event_id                    uuid NOT NULL,
    decided_at                         timestamptz NOT NULL,
    CONSTRAINT fk_protein_decision_proposal_owner FOREIGN KEY
        (user_id, proposal_id)
        REFERENCES protein_target_proposal(user_id, proposal_id),
    CONSTRAINT fk_protein_decision_target_owner FOREIGN KEY
        (user_id, resulting_target_policy_version_id)
        REFERENCES target_policy_version(user_id, version_id),
    CONSTRAINT uq_protein_proposal_terminal UNIQUE (user_id, proposal_id),
    CONSTRAINT uq_protein_proposal_decision_event UNIQUE (user_id, client_event_id),
    CONSTRAINT chk_protein_proposal_decision_result CHECK (
        (decision = 'approved' AND resulting_target_policy_version_id IS NOT NULL)
        OR (decision = 'rejected' AND resulting_target_policy_version_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_protein_proposal_decision_recent
    ON protein_target_proposal_decision (user_id, decided_at DESC, decision_id DESC);

CREATE TABLE IF NOT EXISTS next_meal_recommendation (
    recommendation_id       uuid PRIMARY KEY,
    user_id                 uuid NOT NULL,
    client_request_id       uuid NOT NULL,
    local_date              date NOT NULL,
    timezone                text NOT NULL,
    decision_at             timestamptz NOT NULL,
    target_policy_version_id uuid,
    status                  text NOT NULL CHECK (status IN (
                                'recommended',
                                'no_approved_target_policy',
                                'no_approved_calorie_target',
                                'no_approved_protein_target',
                                'unsupported_target_semantics',
                                'incomplete_ledger_nutrition',
                                'no_remaining_meal_opportunity',
                                'daily_calorie_target_met',
                                'menu_data_unavailable',
                                'stale_menu_data',
                                'no_eligible_candidate'
                            )),
    reason_codes            text[] NOT NULL,
    inputs_digest           text NOT NULL CHECK (inputs_digest ~ '^[0-9a-f]{64}$'),
    artifact_jsonb          jsonb NOT NULL,
    artifact_sha256         text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    created_at              timestamptz NOT NULL,
    CONSTRAINT fk_next_meal_target_owner FOREIGN KEY
        (user_id, target_policy_version_id)
        REFERENCES target_policy_version(user_id, version_id),
    CONSTRAINT uq_next_meal_user_request UNIQUE (user_id, client_request_id),
    CONSTRAINT uq_next_meal_user_id UNIQUE (user_id, recommendation_id)
);

CREATE INDEX IF NOT EXISTS ix_next_meal_user_recent
    ON next_meal_recommendation (user_id, created_at DESC, recommendation_id DESC);

ALTER TABLE protein_target_proposal ENABLE ROW LEVEL SECURITY;
ALTER TABLE protein_target_proposal_decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE next_meal_recommendation ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS protein_target_proposal_owner_all ON protein_target_proposal;
CREATE POLICY protein_target_proposal_owner_all ON protein_target_proposal
    FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS protein_target_proposal_decision_owner_all
    ON protein_target_proposal_decision;
CREATE POLICY protein_target_proposal_decision_owner_all
    ON protein_target_proposal_decision FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS next_meal_recommendation_owner_all ON next_meal_recommendation;
CREATE POLICY next_meal_recommendation_owner_all ON next_meal_recommendation
    FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

REVOKE ALL ON TABLE protein_target_proposal,
    protein_target_proposal_decision, next_meal_recommendation FROM authenticated;
GRANT SELECT, INSERT ON TABLE protein_target_proposal,
    protein_target_proposal_decision, next_meal_recommendation TO authenticated;
