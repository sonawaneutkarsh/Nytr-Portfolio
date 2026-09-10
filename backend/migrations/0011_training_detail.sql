-- M14A: immutable source revisions for detailed strength-training structure.
--
-- These rows are additive to the high-level HealthKit training_session ledger.
-- A source deletion hides detailed revisions but never deletes or changes the
-- corresponding HealthKit observation.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS training_detail_session_revision (
    revision_id             uuid PRIMARY KEY,
    user_id                 uuid NOT NULL,
    source_system           text NOT NULL,
    source_session_id       text NOT NULL,
    source_revision         text NOT NULL,
    title                   text NOT NULL,
    description             text,
    routine_id              text,
    started_at              timestamptz NOT NULL,
    ended_at                timestamptz NOT NULL,
    source_created_at       timestamptz,
    source_updated_at       timestamptz,
    parser_version          text NOT NULL,
    source_payload_sha256   text NOT NULL,
    ingested_at             timestamptz NOT NULL,
    CONSTRAINT uq_training_detail_source_revision
        UNIQUE (user_id, source_system, source_session_id, source_revision),
    CONSTRAINT uq_training_detail_revision_owner
        UNIQUE (revision_id, user_id),
    CONSTRAINT chk_training_detail_source_system
        CHECK (length(btrim(source_system)) > 0),
    CONSTRAINT chk_training_detail_source_session_id
        CHECK (length(btrim(source_session_id)) > 0),
    CONSTRAINT chk_training_detail_source_revision
        CHECK (length(btrim(source_revision)) > 0),
    CONSTRAINT chk_training_detail_title
        CHECK (length(btrim(title)) > 0),
    CONSTRAINT chk_training_detail_time_order
        CHECK (ended_at >= started_at),
    CONSTRAINT chk_training_detail_parser_version
        CHECK (length(btrim(parser_version)) > 0),
    CONSTRAINT chk_training_detail_payload_sha256
        CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS training_detail_exercise (
    exercise_id              uuid PRIMARY KEY,
    user_id                  uuid NOT NULL,
    revision_id              uuid NOT NULL,
    occurrence_identity      text NOT NULL,
    source_exercise_id       text NOT NULL,
    display_name             text NOT NULL,
    exercise_order           integer NOT NULL,
    notes                    text,
    superset_id              integer,
    CONSTRAINT fk_training_detail_exercise_revision
        FOREIGN KEY (revision_id, user_id)
        REFERENCES training_detail_session_revision(revision_id, user_id),
    CONSTRAINT uq_training_detail_exercise_occurrence
        UNIQUE (revision_id, occurrence_identity),
    CONSTRAINT uq_training_detail_exercise_order
        UNIQUE (revision_id, exercise_order),
    CONSTRAINT uq_training_detail_exercise_owner
        UNIQUE (exercise_id, user_id),
    CONSTRAINT chk_training_detail_occurrence_identity
        CHECK (length(btrim(occurrence_identity)) > 0),
    CONSTRAINT chk_training_detail_source_exercise_id
        CHECK (length(btrim(source_exercise_id)) > 0),
    CONSTRAINT chk_training_detail_display_name
        CHECK (length(btrim(display_name)) > 0),
    CONSTRAINT chk_training_detail_exercise_order
        CHECK (exercise_order >= 0),
    CONSTRAINT chk_training_detail_superset
        CHECK (superset_id IS NULL OR superset_id >= 0)
);

CREATE TABLE IF NOT EXISTS training_detail_set (
    set_id                  uuid PRIMARY KEY,
    user_id                uuid NOT NULL,
    exercise_id            uuid NOT NULL,
    set_identity           text NOT NULL,
    source_set_id          text,
    set_index              integer NOT NULL,
    set_type               text NOT NULL,
    reps                   integer,
    load_value             numeric,
    load_unit              text,
    distance_value         numeric,
    distance_unit          text,
    duration_seconds       numeric,
    rpe                    numeric,
    custom_metric          numeric,
    CONSTRAINT fk_training_detail_set_exercise
        FOREIGN KEY (exercise_id, user_id)
        REFERENCES training_detail_exercise(exercise_id, user_id),
    CONSTRAINT uq_training_detail_set_identity
        UNIQUE (exercise_id, set_identity),
    CONSTRAINT uq_training_detail_set_order
        UNIQUE (exercise_id, set_index),
    CONSTRAINT chk_training_detail_set_identity
        CHECK (length(btrim(set_identity)) > 0),
    CONSTRAINT chk_training_detail_set_type
        CHECK (set_type IN ('normal', 'warmup', 'dropset', 'failure')),
    CONSTRAINT chk_training_detail_set_index
        CHECK (set_index >= 0),
    CONSTRAINT chk_training_detail_reps
        CHECK (reps IS NULL OR reps >= 0),
    CONSTRAINT chk_training_detail_load_pair
        CHECK ((load_value IS NULL) = (load_unit IS NULL)),
    CONSTRAINT chk_training_detail_load_unit
        CHECK (load_unit IS NULL OR load_unit IN ('kg', 'lb')),
    CONSTRAINT chk_training_detail_distance_pair
        CHECK ((distance_value IS NULL) = (distance_unit IS NULL)),
    CONSTRAINT chk_training_detail_distance
        CHECK (distance_value IS NULL OR distance_value >= 0),
    CONSTRAINT chk_training_detail_distance_unit
        CHECK (distance_unit IS NULL OR distance_unit = 'm'),
    CONSTRAINT chk_training_detail_duration
        CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    CONSTRAINT chk_training_detail_rpe
        CHECK (rpe IS NULL OR rpe >= 0),
    CONSTRAINT chk_training_detail_custom_metric
        CHECK (custom_metric IS NULL OR custom_metric >= 0)
);

CREATE TABLE IF NOT EXISTS training_detail_session_tombstone (
    tombstone_id           uuid PRIMARY KEY,
    user_id                uuid NOT NULL,
    source_system          text NOT NULL,
    source_session_id      text NOT NULL,
    deleted_at             timestamptz NOT NULL,
    parser_version         text NOT NULL,
    source_payload_sha256  text NOT NULL,
    observed_at            timestamptz NOT NULL,
    CONSTRAINT uq_training_detail_tombstone_identity
        UNIQUE (user_id, source_system, source_session_id),
    CONSTRAINT chk_training_detail_tombstone_source
        CHECK (length(btrim(source_system)) > 0),
    CONSTRAINT chk_training_detail_tombstone_session
        CHECK (length(btrim(source_session_id)) > 0),
    CONSTRAINT chk_training_detail_tombstone_parser
        CHECK (length(btrim(parser_version)) > 0),
    CONSTRAINT chk_training_detail_tombstone_sha256
        CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS ix_training_detail_owner_recent
    ON training_detail_session_revision (user_id, started_at DESC, revision_id DESC);

CREATE OR REPLACE FUNCTION lock_training_detail_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            NEW.user_id::text || chr(31) || NEW.source_system || chr(31)
                || NEW.source_session_id,
            0
        )
    );
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION prevent_training_detail_resurrection()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            NEW.user_id::text || chr(31) || NEW.source_system || chr(31)
                || NEW.source_session_id,
            0
        )
    );
    IF EXISTS (
        SELECT 1 FROM training_detail_session_tombstone
        WHERE user_id=NEW.user_id
          AND source_system=NEW.source_system
          AND source_session_id=NEW.source_session_id
    ) THEN
        RAISE EXCEPTION 'tombstoned detailed training identity cannot be reinserted'
            USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_training_detail_prevent_resurrection
    ON training_detail_session_revision;
CREATE TRIGGER trg_training_detail_prevent_resurrection
BEFORE INSERT ON training_detail_session_revision
FOR EACH ROW EXECUTE FUNCTION prevent_training_detail_resurrection();

DROP TRIGGER IF EXISTS trg_training_detail_tombstone_lock
    ON training_detail_session_tombstone;
CREATE TRIGGER trg_training_detail_tombstone_lock
BEFORE INSERT ON training_detail_session_tombstone
FOR EACH ROW EXECUTE FUNCTION lock_training_detail_identity();

ALTER TABLE training_detail_session_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_detail_exercise ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_detail_set ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_detail_session_tombstone ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS training_detail_revision_owner_select
    ON training_detail_session_revision;
CREATE POLICY training_detail_revision_owner_select ON training_detail_session_revision
    FOR SELECT TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);
DROP POLICY IF EXISTS training_detail_revision_owner_insert
    ON training_detail_session_revision;
CREATE POLICY training_detail_revision_owner_insert ON training_detail_session_revision
    FOR INSERT TO authenticated
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS training_detail_exercise_owner_select ON training_detail_exercise;
CREATE POLICY training_detail_exercise_owner_select ON training_detail_exercise
    FOR SELECT TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);
DROP POLICY IF EXISTS training_detail_exercise_owner_insert ON training_detail_exercise;
CREATE POLICY training_detail_exercise_owner_insert ON training_detail_exercise
    FOR INSERT TO authenticated
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS training_detail_set_owner_select ON training_detail_set;
CREATE POLICY training_detail_set_owner_select ON training_detail_set
    FOR SELECT TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);
DROP POLICY IF EXISTS training_detail_set_owner_insert ON training_detail_set;
CREATE POLICY training_detail_set_owner_insert ON training_detail_set
    FOR INSERT TO authenticated
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS training_detail_tombstone_owner_select
    ON training_detail_session_tombstone;
CREATE POLICY training_detail_tombstone_owner_select ON training_detail_session_tombstone
    FOR SELECT TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);
DROP POLICY IF EXISTS training_detail_tombstone_owner_insert
    ON training_detail_session_tombstone;
CREATE POLICY training_detail_tombstone_owner_insert ON training_detail_session_tombstone
    FOR INSERT TO authenticated
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

REVOKE ALL ON training_detail_session_revision FROM authenticated;
GRANT SELECT, INSERT ON training_detail_session_revision TO authenticated;
REVOKE ALL ON training_detail_exercise FROM authenticated;
GRANT SELECT, INSERT ON training_detail_exercise TO authenticated;
REVOKE ALL ON training_detail_set FROM authenticated;
GRANT SELECT, INSERT ON training_detail_set TO authenticated;
REVOKE ALL ON training_detail_session_tombstone FROM authenticated;
GRANT SELECT, INSERT ON training_detail_session_tombstone TO authenticated;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;
