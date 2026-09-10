-- M12A: immutable HealthKit workout observations (ADR-036).
--
-- training_session stores complete source observations. A separate immutable
-- identity tombstone is required for deletion-before-add: the approved
-- training_session timestamps are NOT NULL, so inventing a fake session merely
-- to remember an early HKDeletedObject would corrupt historical meaning.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS training_session (
    session_id               uuid PRIMARY KEY,
    user_id                  uuid NOT NULL,
    source_system            text NOT NULL,
    source_record_id         text NOT NULL,
    activity_type            text,
    started_at               timestamptz NOT NULL,
    ended_at                 timestamptz NOT NULL,
    active_duration_seconds  numeric,
    active_energy_kcal       numeric,
    timezone_identifier      text,
    source_name              text,
    source_bundle_id         text,
    source_revision          text,
    ingested_at              timestamptz NOT NULL DEFAULT now(),
    tombstoned_at            timestamptz,
    CONSTRAINT uq_training_session_source_identity
        UNIQUE (user_id, source_system, source_record_id),
    CONSTRAINT chk_training_session_source_system
        CHECK (length(btrim(source_system)) > 0),
    CONSTRAINT chk_training_session_source_record_id
        CHECK (length(btrim(source_record_id)) > 0),
    CONSTRAINT chk_training_session_time_order
        CHECK (ended_at >= started_at),
    CONSTRAINT chk_training_session_duration
        CHECK (active_duration_seconds IS NULL OR active_duration_seconds >= 0),
    CONSTRAINT chk_training_session_energy
        CHECK (active_energy_kcal IS NULL OR active_energy_kcal >= 0)
);

CREATE TABLE IF NOT EXISTS training_session_tombstone (
    tombstone_id       uuid PRIMARY KEY,
    user_id            uuid NOT NULL,
    source_system      text NOT NULL,
    source_record_id   text NOT NULL,
    tombstoned_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_training_tombstone_source_identity
        UNIQUE (user_id, source_system, source_record_id),
    CONSTRAINT chk_training_tombstone_source_system
        CHECK (length(btrim(source_system)) > 0),
    CONSTRAINT chk_training_tombstone_source_record_id
        CHECK (length(btrim(source_record_id)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_training_session_user_recent
    ON training_session (user_id, started_at DESC)
    WHERE tombstoned_at IS NULL;

-- Serialize add/delete decisions for one source identity and reject any add
-- after a tombstone has won. This makes deletion-before-add durable even when
-- two ingestion transactions for the same HealthKit UUID overlap.
CREATE OR REPLACE FUNCTION lock_training_session_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            NEW.user_id::text || chr(31) || NEW.source_system || chr(31)
                || NEW.source_record_id,
            0
        )
    );
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION prevent_training_session_resurrection()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            NEW.user_id::text || chr(31) || NEW.source_system || chr(31)
                || NEW.source_record_id,
            0
        )
    );
    IF EXISTS (
        SELECT 1
        FROM training_session_tombstone
        WHERE user_id = NEW.user_id
          AND source_system = NEW.source_system
          AND source_record_id = NEW.source_record_id
    ) THEN
        RAISE EXCEPTION 'tombstoned training_session identity cannot be reinserted'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_training_session_prevent_resurrection ON training_session;
CREATE TRIGGER trg_training_session_prevent_resurrection
BEFORE INSERT ON training_session
FOR EACH ROW
EXECUTE FUNCTION prevent_training_session_resurrection();

DROP TRIGGER IF EXISTS trg_training_tombstone_lock_identity ON training_session_tombstone;
CREATE TRIGGER trg_training_tombstone_lock_identity
BEFORE INSERT ON training_session_tombstone
FOR EACH ROW
EXECUTE FUNCTION lock_training_session_identity();

-- Observation columns are immutable. The sole legal transition is the first
-- NULL -> non-NULL tombstoned_at change; the first tombstone timestamp wins.
CREATE OR REPLACE FUNCTION enforce_training_session_tombstone_only_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.user_id IS DISTINCT FROM OLD.user_id
       OR NEW.source_system IS DISTINCT FROM OLD.source_system
       OR NEW.source_record_id IS DISTINCT FROM OLD.source_record_id
       OR NEW.activity_type IS DISTINCT FROM OLD.activity_type
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.ended_at IS DISTINCT FROM OLD.ended_at
       OR NEW.active_duration_seconds IS DISTINCT FROM OLD.active_duration_seconds
       OR NEW.active_energy_kcal IS DISTINCT FROM OLD.active_energy_kcal
       OR NEW.timezone_identifier IS DISTINCT FROM OLD.timezone_identifier
       OR NEW.source_name IS DISTINCT FROM OLD.source_name
       OR NEW.source_bundle_id IS DISTINCT FROM OLD.source_bundle_id
       OR NEW.source_revision IS DISTINCT FROM OLD.source_revision
       OR NEW.ingested_at IS DISTINCT FROM OLD.ingested_at
       OR OLD.tombstoned_at IS NOT NULL
       OR NEW.tombstoned_at IS NULL THEN
        RAISE EXCEPTION 'training_session observations are immutable; only first tombstoning is allowed'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_training_session_tombstone_only_update ON training_session;
CREATE TRIGGER trg_training_session_tombstone_only_update
BEFORE UPDATE ON training_session
FOR EACH ROW
EXECUTE FUNCTION enforce_training_session_tombstone_only_update();

ALTER TABLE training_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_session_tombstone ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS training_session_owner_select ON training_session;
CREATE POLICY training_session_owner_select ON training_session
    FOR SELECT TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

DROP POLICY IF EXISTS training_session_owner_insert ON training_session;
CREATE POLICY training_session_owner_insert ON training_session
    FOR INSERT TO authenticated
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

DROP POLICY IF EXISTS training_session_owner_tombstone ON training_session;
CREATE POLICY training_session_owner_tombstone ON training_session
    FOR UPDATE TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    )
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

DROP POLICY IF EXISTS training_tombstone_owner_select ON training_session_tombstone;
CREATE POLICY training_tombstone_owner_select ON training_session_tombstone
    FOR SELECT TO authenticated
    USING (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

DROP POLICY IF EXISTS training_tombstone_owner_insert ON training_session_tombstone;
CREATE POLICY training_tombstone_owner_insert ON training_session_tombstone
    FOR INSERT TO authenticated
    WITH CHECK (
        user_id = NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
    );

REVOKE ALL ON training_session FROM authenticated;
GRANT SELECT, INSERT ON training_session TO authenticated;
GRANT UPDATE (tombstoned_at) ON training_session TO authenticated;

REVOKE ALL ON training_session_tombstone FROM authenticated;
GRANT SELECT, INSERT ON training_session_tombstone TO authenticated;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;
