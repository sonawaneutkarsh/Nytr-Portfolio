-- Milestone 2 schema: Stacks ingestion pipeline (ADR-013).
-- Scope: ingestion runs, snapshots, foods, versioned nutrition profiles,
-- daily offerings, quarantine records. No planner/health/user/LLM tables.

CREATE TABLE IF NOT EXISTS stacks_ingestion_run (
    run_id              uuid PRIMARY KEY,
    status              text NOT NULL CHECK (status IN (
                            'started','fetching_menu','menu_fetched','menu_parsed',
                            'label_fetching','labels_fetched','normalized','validating',
                            'validated','persisting','persisted','partial_failure',
                            'quarantined','failed','blocked_by_policy')),
    mode                text NOT NULL,
    params              jsonb NOT NULL,
    config_fingerprint  text NOT NULL,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz
);

CREATE TABLE IF NOT EXISTS source_snapshot (
    snapshot_id      uuid PRIMARY KEY,
    source_system    text NOT NULL,
    source_url       text NOT NULL,
    http_method      text NOT NULL,
    request_params   jsonb NOT NULL DEFAULT '{}'::jsonb,
    http_status      int NOT NULL,
    content_sha256   text NOT NULL,
    byte_size        int NOT NULL,
    content_type     text NOT NULL DEFAULT 'text/html',
    fetched_at       timestamptz NOT NULL,
    parser_version   text NOT NULL,
    ingestion_run_id uuid NOT NULL REFERENCES stacks_ingestion_run(run_id),
    retention_class  text NOT NULL DEFAULT 'raw_html_default',
    retain_until     timestamptz,
    storage_path     text NOT NULL,
    UNIQUE (source_system, content_sha256)
);

CREATE TABLE IF NOT EXISTS stacks_food (
    food_id         uuid PRIMARY KEY,
    campus_id       int NOT NULL DEFAULT 50,
    name_raw        text NOT NULL,
    name_normalized text NOT NULL,
    rec_num         text,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (campus_id, name_normalized)
);

CREATE TABLE IF NOT EXISTS nutrition_profile (
    profile_id            uuid PRIMARY KEY,
    food_id               uuid NOT NULL REFERENCES stacks_food(food_id),
    snapshot_id           uuid NOT NULL REFERENCES source_snapshot(snapshot_id),
    parser_version        text NOT NULL,
    serving_basis_raw     text NOT NULL,
    serving_basis_kind    text NOT NULL,
    nutrients             jsonb NOT NULL,
    unavailable_fields    text[] NOT NULL DEFAULT '{}',
    extra_fields          jsonb NOT NULL DEFAULT '{}'::jsonb,
    ingredients_raw       text NOT NULL DEFAULT '',
    ingredient_components jsonb,
    allergens             text[] NOT NULL DEFAULT '{}',
    confidence            text NOT NULL CHECK (confidence IN (
                              'official_published','official_component_sum',
                              'verified_internal_recipe','partial','estimated')),
    captured_at           timestamptz NOT NULL,
    superseded_by         uuid REFERENCES nutrition_profile(profile_id),
    valid_to              timestamptz,
    UNIQUE (food_id, snapshot_id)
);
CREATE INDEX IF NOT EXISTS ix_profile_food_current
    ON nutrition_profile (food_id) WHERE superseded_by IS NULL;

CREATE TABLE IF NOT EXISTS menu_offering (
    offering_id        uuid PRIMARY KEY,
    service_date       date NOT NULL,
    meal_period        text NOT NULL CHECK (meal_period IN ('Breakfast','Lunch','Dinner')),
    campus_id          int NOT NULL DEFAULT 50,
    food_id            uuid NOT NULL REFERENCES stacks_food(food_id),
    occurrence_ordinal int NOT NULL DEFAULT 0,
    category_name      text NOT NULL,
    category_position  int NOT NULL,
    item_position      int NOT NULL,
    source_mid         text NOT NULL,
    dietary_tags       text[] NOT NULL DEFAULT '{}',
    profile_id         uuid REFERENCES nutrition_profile(profile_id),
    snapshot_id        uuid NOT NULL REFERENCES source_snapshot(snapshot_id),
    UNIQUE (service_date, meal_period, campus_id, food_id, occurrence_ordinal)
);
CREATE INDEX IF NOT EXISTS ix_offering_page ON menu_offering (service_date, meal_period);

CREATE TABLE IF NOT EXISTS quarantine_record (
    record_id            uuid PRIMARY KEY,
    run_id               uuid NOT NULL REFERENCES stacks_ingestion_run(run_id),
    code                 text NOT NULL,
    severity             text NOT NULL CHECK (severity IN ('info','warn','error')),
    subject_type         text NOT NULL CHECK (subject_type IN ('run','page','item','label')),
    natural_key          jsonb NOT NULL DEFAULT '{}'::jsonb,
    detail               text NOT NULL DEFAULT '',
    parser_version       text NOT NULL,
    snapshot_ref_sha256  text,
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_quarantine_run ON quarantine_record (run_id);
