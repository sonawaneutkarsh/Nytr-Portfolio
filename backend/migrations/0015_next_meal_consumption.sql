-- M16B: explicit factual consumption of an immutable next-meal recommendation.
-- The selected candidate and nutrition are copied at record time so history
-- never depends on later menu, policy, target, or recommendation behavior.

CREATE TABLE IF NOT EXISTS next_meal_consumption (
    entry_id                       uuid PRIMARY KEY,
    user_id                        uuid NOT NULL,
    recommendation_id              uuid NOT NULL,
    client_event_id                uuid NOT NULL,
    local_date                     date NOT NULL,
    timezone                       text NOT NULL CHECK (btrim(timezone) <> ''),
    recorded_at                    timestamptz NOT NULL,
    state                          text NOT NULL CHECK (state = 'eaten'),
    recommendation_artifact_sha256 text NOT NULL CHECK (
        recommendation_artifact_sha256 ~ '^[0-9a-f]{64}$'
    ),
    next_meal_policy_version       text NOT NULL CHECK (
        btrim(next_meal_policy_version) <> ''
    ),
    meal_context                   text NOT NULL CHECK (btrim(meal_context) <> ''),
    menu_period                    text NOT NULL CHECK (btrim(menu_period) <> ''),
    candidate_id                   text NOT NULL CHECK (btrim(candidate_id) <> ''),
    item_name                      text NOT NULL CHECK (btrim(item_name) <> ''),
    serving_description            text NOT NULL CHECK (btrim(serving_description) <> ''),
    configuration_summary          text,
    nutrition_authority            text NOT NULL CHECK (nutrition_authority IN (
                                       'official','estimated','partial'
                                   )),
    nutrition_confidence           text,
    calories_kcal                  numeric CHECK (
        calories_kcal IS NULL OR (
            calories_kcal >= 0
            AND calories_kcal NOT IN ('NaN'::numeric,'Infinity'::numeric)
        )
    ),
    protein_g                      numeric CHECK (
        protein_g IS NULL OR (
            protein_g >= 0
            AND protein_g NOT IN ('NaN'::numeric,'Infinity'::numeric)
        )
    ),
    unknown_nutrients              text[] NOT NULL,
    selected_candidate_jsonb       jsonb NOT NULL,
    selected_candidate_sha256      text NOT NULL CHECK (
        selected_candidate_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT fk_next_meal_consumption_recommendation_owner FOREIGN KEY
        (user_id, recommendation_id)
        REFERENCES next_meal_recommendation(user_id, recommendation_id),
    CONSTRAINT uq_next_meal_consumption_event UNIQUE (user_id, client_event_id),
    CONSTRAINT uq_next_meal_consumption_recommendation UNIQUE (
        user_id, recommendation_id
    )
);

CREATE INDEX IF NOT EXISTS ix_next_meal_consumption_user_recent
    ON next_meal_consumption (user_id, recorded_at DESC, entry_id DESC);

ALTER TABLE next_meal_consumption ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS next_meal_consumption_owner_all ON next_meal_consumption;
CREATE POLICY next_meal_consumption_owner_all ON next_meal_consumption
    FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

REVOKE ALL ON TABLE next_meal_consumption FROM authenticated;
GRANT SELECT, INSERT ON TABLE next_meal_consumption TO authenticated;
