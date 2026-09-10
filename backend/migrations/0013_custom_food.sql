-- M15: immutable owner-entered food versions and append-only manual intake.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS custom_food (
    food_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_custom_food_owner UNIQUE (food_id, user_id)
);

CREATE TABLE IF NOT EXISTS custom_food_version (
    version_id uuid PRIMARY KEY,
    food_id uuid NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    brand text NULL,
    serving_description text NOT NULL,
    serving_amount numeric NOT NULL,
    serving_unit text NOT NULL,
    calories_kcal numeric NULL,
    protein_g numeric NULL,
    carbohydrate_g numeric NULL,
    total_fat_g numeric NULL,
    fiber_g numeric NULL,
    sodium_mg numeric NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT fk_custom_food_version_owner
        FOREIGN KEY (food_id, user_id) REFERENCES custom_food(food_id, user_id),
    CONSTRAINT uq_custom_food_version_owner UNIQUE (version_id, food_id, user_id),
    CONSTRAINT chk_custom_food_version_text CHECK (
        length(btrim(name)) > 0
        AND length(btrim(serving_description)) > 0
        AND length(btrim(serving_unit)) > 0
        AND (brand IS NULL OR length(btrim(brand)) > 0)
    ),
    CONSTRAINT chk_custom_food_version_serving CHECK (
        serving_amount > 0
        AND serving_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric)
    ),
    CONSTRAINT chk_custom_food_version_nutrition CHECK (
        COALESCE(calories_kcal, protein_g, carbohydrate_g, total_fat_g,
                 fiber_g, sodium_mg) IS NOT NULL
        AND calories_kcal >= 0 AND protein_g >= 0 AND carbohydrate_g >= 0
        AND total_fat_g >= 0 AND fiber_g >= 0 AND sodium_mg >= 0
        AND COALESCE(calories_kcal NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(protein_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(carbohydrate_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(total_fat_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(fiber_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(sodium_mg NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
    )
);

CREATE TABLE IF NOT EXISTS manual_food_consumption (
    entry_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    food_id uuid NOT NULL,
    food_version_id uuid NOT NULL,
    client_event_id uuid NOT NULL,
    source_system text NOT NULL DEFAULT 'manual_custom',
    meal_period text NOT NULL,
    consumed_amount numeric NOT NULL,
    consumed_unit text NOT NULL,
    portion_factor numeric NOT NULL,
    food_name text NOT NULL,
    brand text NULL,
    serving_description text NOT NULL,
    serving_amount numeric NOT NULL,
    serving_unit text NOT NULL,
    calories_kcal numeric NULL,
    protein_g numeric NULL,
    carbohydrate_g numeric NULL,
    total_fat_g numeric NULL,
    fiber_g numeric NULL,
    sodium_mg numeric NULL,
    recorded_at timestamptz NOT NULL,
    CONSTRAINT fk_manual_food_consumption_version
        FOREIGN KEY (food_version_id, food_id, user_id)
        REFERENCES custom_food_version(version_id, food_id, user_id),
    CONSTRAINT uq_manual_food_consumption_event UNIQUE (user_id, client_event_id),
    CONSTRAINT chk_manual_food_source CHECK (source_system = 'manual_custom'),
    CONSTRAINT chk_manual_food_period CHECK (meal_period IN ('breakfast','lunch','dinner')),
    CONSTRAINT chk_manual_food_amounts CHECK (
        consumed_amount > 0 AND portion_factor > 0 AND serving_amount > 0
        AND consumed_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric)
        AND portion_factor NOT IN ('NaN'::numeric, 'Infinity'::numeric)
        AND serving_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric)
    ),
    CONSTRAINT chk_manual_food_text CHECK (
        length(btrim(consumed_unit)) > 0 AND length(btrim(food_name)) > 0
        AND length(btrim(serving_description)) > 0 AND length(btrim(serving_unit)) > 0
    ),
    CONSTRAINT chk_manual_food_nutrition CHECK (
        COALESCE(calories_kcal, protein_g, carbohydrate_g, total_fat_g,
                 fiber_g, sodium_mg) IS NOT NULL
        AND calories_kcal >= 0 AND protein_g >= 0 AND carbohydrate_g >= 0
        AND total_fat_g >= 0 AND fiber_g >= 0 AND sodium_mg >= 0
        AND COALESCE(calories_kcal NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(protein_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(carbohydrate_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(total_fat_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(fiber_g NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
        AND COALESCE(sodium_mg NOT IN ('NaN'::numeric, 'Infinity'::numeric), true)
    )
);

CREATE INDEX IF NOT EXISTS ix_custom_food_version_owner_latest
    ON custom_food_version (user_id, food_id, created_at DESC, version_id DESC);
CREATE INDEX IF NOT EXISTS ix_manual_food_consumption_owner_recorded
    ON manual_food_consumption (user_id, recorded_at, entry_id);

ALTER TABLE custom_food ENABLE ROW LEVEL SECURITY;
ALTER TABLE custom_food_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE manual_food_consumption ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS custom_food_owner_all ON custom_food;
CREATE POLICY custom_food_owner_all ON custom_food FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS custom_food_version_owner_all ON custom_food_version;
CREATE POLICY custom_food_version_owner_all ON custom_food_version FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (
        user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM custom_food f
            WHERE f.food_id=custom_food_version.food_id
              AND f.user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS manual_food_consumption_owner_all ON manual_food_consumption;
CREATE POLICY manual_food_consumption_owner_all ON manual_food_consumption FOR ALL TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid)
    WITH CHECK (
        user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM custom_food_version v
            WHERE v.version_id=manual_food_consumption.food_version_id
              AND v.food_id=manual_food_consumption.food_id
              AND v.user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        )
    );

REVOKE ALL ON custom_food, custom_food_version, manual_food_consumption FROM authenticated;
GRANT SELECT, INSERT ON custom_food, custom_food_version, manual_food_consumption TO authenticated;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;
