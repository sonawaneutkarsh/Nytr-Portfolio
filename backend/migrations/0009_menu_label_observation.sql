-- M11C: immutable per-label evidence for resumable, still-page-atomic ingestion.
-- These rows are trusted global Stacks source data. They can satisfy only the
-- exact logical page occurrence recorded below; they do not make a menu page
-- available to the planner.

CREATE TABLE IF NOT EXISTS menu_label_observation (
    observation_id          uuid PRIMARY KEY,
    service_date            date NOT NULL,
    meal_period             text NOT NULL CHECK (
                                meal_period IN ('Breakfast','Lunch','Dinner')
                            ),
    campus_id               integer NOT NULL,
    menu_snapshot_id        uuid NOT NULL
                                REFERENCES source_snapshot(snapshot_id),
    name_normalized         text NOT NULL CHECK (btrim(name_normalized) <> ''),
    occurrence_ordinal      integer NOT NULL CHECK (occurrence_ordinal >= 0),
    source_mid              text NOT NULL CHECK (btrim(source_mid) <> ''),
    food_id                 uuid NOT NULL REFERENCES stacks_food(food_id),
    nutrition_source_state  text NOT NULL CHECK (
                                nutrition_source_state IN (
                                    'profile_available',
                                    'source_placeholder',
                                    'source_incomplete',
                                    'source_unavailable'
                                )
                            ),
    profile_id              uuid REFERENCES nutrition_profile(profile_id),
    nutrition_snapshot_id   uuid NOT NULL
                                REFERENCES source_snapshot(snapshot_id),
    parser_version          text NOT NULL CHECK (btrim(parser_version) <> ''),
    ingestion_run_id        uuid NOT NULL
                                REFERENCES stacks_ingestion_run(run_id),
    recorded_at             timestamptz NOT NULL,
    CONSTRAINT chk_menu_label_observation_profile_state CHECK (
        (
            nutrition_source_state = 'profile_available'
            AND profile_id IS NOT NULL
        )
        OR
        (
            nutrition_source_state IN (
                'source_placeholder',
                'source_incomplete',
                'source_unavailable'
            )
            AND profile_id IS NULL
        )
    ),
    CONSTRAINT uq_menu_label_observation_evidence UNIQUE (
        service_date,
        meal_period,
        campus_id,
        name_normalized,
        occurrence_ordinal,
        source_mid,
        parser_version,
        nutrition_snapshot_id
    )
);

CREATE INDEX IF NOT EXISTS ix_menu_label_observation_lookup
    ON menu_label_observation (
        service_date,
        meal_period,
        campus_id,
        source_mid,
        name_normalized,
        occurrence_ordinal,
        recorded_at DESC,
        observation_id DESC
    );

-- This table is backend-internal global source evidence. The API remains the
-- external boundary; authenticated clients receive no direct table access.
REVOKE ALL PRIVILEGES ON TABLE menu_label_observation FROM authenticated;
