-- M7 Step 5A: authoritative immutable accepted menu-page versions.
-- Global/shared Stacks source data: trusted backend access only, no RLS.
-- Pre-0005 rows are deliberately not backfilled because their page acceptance
-- and complete membership cannot be proven.

CREATE TABLE IF NOT EXISTS menu_page_version (
    page_version_id  uuid PRIMARY KEY,
    service_date     date NOT NULL,
    meal_period      text NOT NULL CHECK (
                         meal_period IN ('Breakfast','Lunch','Dinner')
                     ),
    campus_id        integer NOT NULL,
    snapshot_id      uuid NOT NULL REFERENCES source_snapshot(snapshot_id),
    ingestion_run_id uuid NOT NULL REFERENCES stacks_ingestion_run(run_id),
    validation_state text NOT NULL CHECK (
                         validation_state IN (
                             'validated_nonempty','validated_empty'
                         )
                     ),
    offering_count   integer NOT NULL CHECK (offering_count >= 0),
    accepted_at      timestamptz NOT NULL,
    parser_version   text NOT NULL,
    CONSTRAINT chk_menu_page_version_state_count CHECK (
        (validation_state = 'validated_empty' AND offering_count = 0)
        OR
        (validation_state = 'validated_nonempty' AND offering_count > 0)
    ),
    CONSTRAINT uq_menu_page_version_source UNIQUE (
        service_date, meal_period, campus_id, snapshot_id
    )
);

CREATE INDEX IF NOT EXISTS ix_menu_page_version_lookup
    ON menu_page_version (service_date, meal_period, campus_id);

CREATE TABLE IF NOT EXISTS menu_page_offering (
    page_version_id uuid NOT NULL
        REFERENCES menu_page_version(page_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    offering_id uuid NOT NULL REFERENCES menu_offering(offering_id),
    food_id uuid NOT NULL REFERENCES stacks_food(food_id),
    profile_id uuid NOT NULL REFERENCES nutrition_profile(profile_id),
    name_normalized text NOT NULL,
    occurrence_ordinal integer NOT NULL,
    category_name text NOT NULL,
    category_position integer NOT NULL,
    item_position integer NOT NULL,
    source_mid text NOT NULL,
    dietary_tags text[] NOT NULL,
    PRIMARY KEY (page_version_id, offering_id)
);

-- Repair an empty unreleased 0005 draft on reapplication. No values are
-- fabricated for draft membership rows: a throwaway developer database with
-- such rows must be recreated so only corrected ingestion can author pins.
ALTER TABLE menu_page_offering
    ADD COLUMN IF NOT EXISTS food_id uuid REFERENCES stacks_food(food_id),
    ADD COLUMN IF NOT EXISTS profile_id uuid REFERENCES nutrition_profile(profile_id),
    ADD COLUMN IF NOT EXISTS name_normalized text,
    ADD COLUMN IF NOT EXISTS occurrence_ordinal integer,
    ADD COLUMN IF NOT EXISTS category_name text,
    ADD COLUMN IF NOT EXISTS category_position integer,
    ADD COLUMN IF NOT EXISTS item_position integer,
    ADD COLUMN IF NOT EXISTS source_mid text,
    ADD COLUMN IF NOT EXISTS dietary_tags text[];

ALTER TABLE menu_page_offering
    ALTER COLUMN food_id SET NOT NULL,
    ALTER COLUMN profile_id SET NOT NULL,
    ALTER COLUMN name_normalized SET NOT NULL,
    ALTER COLUMN occurrence_ordinal SET NOT NULL,
    ALTER COLUMN category_name SET NOT NULL,
    ALTER COLUMN category_position SET NOT NULL,
    ALTER COLUMN item_position SET NOT NULL,
    ALTER COLUMN source_mid SET NOT NULL,
    ALTER COLUMN dietary_tags SET NOT NULL;

-- These tables are backend-internal global source data. The external boundary
-- remains the API; the Supabase authenticated role receives no direct access.
REVOKE ALL PRIVILEGES ON TABLE menu_page_version, menu_page_offering
    FROM authenticated;
