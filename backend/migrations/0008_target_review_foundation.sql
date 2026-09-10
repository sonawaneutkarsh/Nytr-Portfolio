-- M10A: immutable goal intent and deterministic target-review evidence.
-- All tables are user-owned, append-only, and protected by JWT-subject RLS.
-- This migration creates no automatic target mutation or public API.

CREATE TABLE IF NOT EXISTS goal_policy_version (
    version_id                 uuid PRIMARY KEY,
    user_id                    uuid NOT NULL,
    policy_version             text NOT NULL,
    direction                  text NOT NULL CHECK (
                                   direction IN ('maintain','gain','lose')
                               ),
    desired_rate_kg_per_week   numeric NOT NULL,
    payload_sha256             text NOT NULL CHECK (length(payload_sha256) = 64),
    created_at                 timestamptz NOT NULL,
    CONSTRAINT chk_goal_policy_direction_rate CHECK (
        (direction = 'gain' AND desired_rate_kg_per_week > 0)
        OR (direction = 'lose' AND desired_rate_kg_per_week < 0)
        OR (direction = 'maintain' AND desired_rate_kg_per_week = 0)
    ),
    CONSTRAINT uq_goal_policy_user_version UNIQUE (user_id, policy_version),
    CONSTRAINT uq_goal_policy_user_payload UNIQUE (user_id, payload_sha256)
);

CREATE INDEX IF NOT EXISTS ix_goal_policy_user_recent
    ON goal_policy_version (user_id, created_at DESC, version_id DESC);

CREATE TABLE IF NOT EXISTS target_review (
    review_id                       uuid PRIMARY KEY,
    user_id                         uuid NOT NULL,
    as_of_date                      date NOT NULL,
    timezone                        text NOT NULL,
    trend_algorithm_version         text NOT NULL,
    trend_input_digest              text NOT NULL CHECK (length(trend_input_digest) = 64),
    goal_policy_version_id          uuid NOT NULL
                                        REFERENCES goal_policy_version(version_id),
    prior_target_policy_version_id  uuid NOT NULL
                                        REFERENCES target_policy_version(version_id),
    review_policy_version           text NOT NULL,
    status                          text NOT NULL CHECK (
        status IN (
            'evidence_unavailable',
            'cooldown_hold',
            'within_band',
            'bound_hold',
            'recommendation_ready'
        )
    ),
    reason_codes                    text[] NOT NULL,
    current_calorie_target          numeric NOT NULL CHECK (current_calorie_target > 0),
    proposed_calorie_target         numeric,
    calorie_delta                   numeric,
    recommendation_digest           text NOT NULL CHECK (length(recommendation_digest) = 64),
    evaluation_payload              jsonb NOT NULL,
    created_at                      timestamptz NOT NULL,
    CONSTRAINT uq_target_review_user_digest UNIQUE (user_id, recommendation_digest),
    CONSTRAINT chk_target_review_proposal_shape CHECK (
        (
            status = 'recommendation_ready'
            AND proposed_calorie_target IS NOT NULL
            AND proposed_calorie_target > 0
            AND calorie_delta IS NOT NULL
            AND calorie_delta <> 0
            AND proposed_calorie_target - current_calorie_target = calorie_delta
        )
        OR
        (
            status <> 'recommendation_ready'
            AND proposed_calorie_target IS NULL
            AND calorie_delta IS NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS ix_target_review_user_recent
    ON target_review (user_id, created_at DESC, review_id DESC);

-- M10B may author these terminal decisions atomically with the resulting
-- target_policy_version + existing decision_log row. M10A creates only the
-- append-only lifecycle constraint; it supplies no decision/apply use case.
CREATE TABLE IF NOT EXISTS target_review_decision (
    decision_id                        uuid PRIMARY KEY,
    user_id                            uuid NOT NULL,
    review_id                          uuid NOT NULL REFERENCES target_review(review_id),
    decision                           text NOT NULL CHECK (decision IN ('approved','rejected')),
    rationale                          text NOT NULL CHECK (btrim(rationale) <> ''),
    resulting_target_policy_version_id uuid
        REFERENCES target_policy_version(version_id),
    client_event_id                    uuid NOT NULL,
    decided_at                         timestamptz NOT NULL,
    CONSTRAINT uq_target_review_terminal_decision UNIQUE (user_id, review_id),
    CONSTRAINT uq_target_review_decision_event UNIQUE (user_id, client_event_id),
    CONSTRAINT chk_target_review_decision_result CHECK (
        (decision = 'approved' AND resulting_target_policy_version_id IS NOT NULL)
        OR (decision = 'rejected' AND resulting_target_policy_version_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_target_review_decision_user_recent
    ON target_review_decision (user_id, decided_at DESC, decision_id DESC);

ALTER TABLE goal_policy_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE target_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE target_review_decision ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS goal_policy_owner_all ON goal_policy_version;
CREATE POLICY goal_policy_owner_all ON goal_policy_version
    FOR ALL TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    )
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

DROP POLICY IF EXISTS target_review_owner_all ON target_review;
CREATE POLICY target_review_owner_all ON target_review
    FOR ALL TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM goal_policy_version goal
            WHERE goal.version_id = target_review.goal_policy_version_id
              AND goal.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
        AND EXISTS (
            SELECT 1 FROM target_policy_version target
            WHERE target.version_id = target_review.prior_target_policy_version_id
              AND target.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    )
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM goal_policy_version goal
            WHERE goal.version_id = target_review.goal_policy_version_id
              AND goal.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
        AND EXISTS (
            SELECT 1 FROM target_policy_version target
            WHERE target.version_id = target_review.prior_target_policy_version_id
              AND target.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS target_review_decision_owner_all ON target_review_decision;
CREATE POLICY target_review_decision_owner_all ON target_review_decision
    FOR ALL TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM target_review review
            WHERE review.review_id = target_review_decision.review_id
              AND review.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
        AND (
            resulting_target_policy_version_id IS NULL
            OR EXISTS (
                SELECT 1 FROM target_policy_version target
                WHERE target.version_id
                    = target_review_decision.resulting_target_policy_version_id
                  AND target.user_id
                      = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
            )
        )
    )
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM target_review review
            WHERE review.review_id = target_review_decision.review_id
              AND review.user_id
                  = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
        AND (
            resulting_target_policy_version_id IS NULL
            OR EXISTS (
                SELECT 1 FROM target_policy_version target
                WHERE target.version_id
                    = target_review_decision.resulting_target_policy_version_id
                  AND target.user_id
                      = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
            )
        )
    );

REVOKE UPDATE, DELETE ON TABLE
    goal_policy_version, target_review, target_review_decision
    FROM authenticated;
GRANT SELECT, INSERT ON TABLE
    goal_policy_version, target_review, target_review_decision
    TO authenticated;
