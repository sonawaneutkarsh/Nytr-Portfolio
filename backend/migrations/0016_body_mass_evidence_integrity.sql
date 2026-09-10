-- M17: database-enforced immutability for HealthKit body-mass evidence.
--
-- Migration 0002 intentionally retained source observations under tombstones,
-- but granted table-wide UPDATE to the authenticated role. Body-mass facts are
-- now immutable after INSERT. The sole legal transition is the first
-- tombstoned_at NULL -> non-NULL update used by HealthKit deletion sync.

CREATE OR REPLACE FUNCTION enforce_health_body_mass_tombstone_only_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.hk_sample_uuid IS DISTINCT FROM OLD.hk_sample_uuid
       OR NEW.metric IS DISTINCT FROM OLD.metric
       OR NEW.value_kg IS DISTINCT FROM OLD.value_kg
       OR NEW.sample_start IS DISTINCT FROM OLD.sample_start
       OR NEW.sample_end IS DISTINCT FROM OLD.sample_end
       OR NEW.source_name IS DISTINCT FROM OLD.source_name
       OR NEW.source_bundle_id IS DISTINCT FROM OLD.source_bundle_id
       OR NEW.ingested_at IS DISTINCT FROM OLD.ingested_at
       OR OLD.tombstoned_at IS NOT NULL
       OR NEW.tombstoned_at IS NULL THEN
        RAISE EXCEPTION 'health_body_mass_sample observations are immutable; only first tombstoning is allowed'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_health_body_mass_tombstone_only_update
    ON health_body_mass_sample;
CREATE TRIGGER trg_health_body_mass_tombstone_only_update
BEFORE UPDATE ON health_body_mass_sample
FOR EACH ROW
EXECUTE FUNCTION enforce_health_body_mass_tombstone_only_update();

REVOKE UPDATE ON TABLE health_body_mass_sample FROM authenticated;
GRANT UPDATE(tombstoned_at) ON TABLE health_body_mass_sample TO authenticated;
