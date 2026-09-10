-- Barcode Food Entry: exact provider identity and immutable source provenance.

ALTER TABLE custom_food
    ADD COLUMN IF NOT EXISTS source_system text NULL,
    ADD COLUMN IF NOT EXISTS source_key text NULL;

ALTER TABLE custom_food_version
    ADD COLUMN IF NOT EXISTS provenance_jsonb jsonb NOT NULL
    DEFAULT '{"authority":"owner_entered"}'::jsonb;

ALTER TABLE manual_food_consumption
    ADD COLUMN IF NOT EXISTS nutrition_authority text NOT NULL DEFAULT 'user_entered',
    ADD COLUMN IF NOT EXISTS nutrition_confidence text NOT NULL DEFAULT 'user_entered',
    ADD COLUMN IF NOT EXISTS provenance_summary text NOT NULL
        DEFAULT 'Owner-entered nutrition frozen at consumption';

ALTER TABLE custom_food DROP CONSTRAINT IF EXISTS chk_custom_food_source_identity;
ALTER TABLE custom_food ADD CONSTRAINT chk_custom_food_source_identity CHECK (
    (source_system IS NULL AND source_key IS NULL)
    OR (
        source_system = 'open_food_facts'
        AND source_key IS NOT NULL
        AND length(btrim(source_key)) > 0
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_food_owner_source
    ON custom_food (user_id, source_system, source_key)
    WHERE source_system IS NOT NULL;

ALTER TABLE custom_food_version DROP CONSTRAINT IF EXISTS chk_custom_food_provenance;
ALTER TABLE custom_food_version ADD CONSTRAINT chk_custom_food_provenance CHECK (
    provenance_jsonb->>'authority' IN ('owner_entered','open_food_facts')
);

ALTER TABLE manual_food_consumption DROP CONSTRAINT IF EXISTS chk_manual_food_source;
ALTER TABLE manual_food_consumption ADD CONSTRAINT chk_manual_food_source CHECK (
    source_system IN ('manual_custom','barcode_open_food_facts')
);
ALTER TABLE manual_food_consumption DROP CONSTRAINT IF EXISTS chk_manual_food_authority;
ALTER TABLE manual_food_consumption ADD CONSTRAINT chk_manual_food_authority CHECK (
    nutrition_authority IN ('user_entered','external_reference')
    AND length(btrim(nutrition_confidence)) > 0
    AND length(btrim(provenance_summary)) > 0
);

REVOKE UPDATE (source_system, source_key) ON custom_food FROM authenticated;
REVOKE UPDATE (provenance_jsonb) ON custom_food_version FROM authenticated;
REVOKE UPDATE (nutrition_authority, nutrition_confidence, provenance_summary)
    ON manual_food_consumption FROM authenticated;
