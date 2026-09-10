-- M14B: append-only owner-scoped continuation for the official Hevy connector.
--
-- The credential remains an environment secret. Only successfully persisted,
-- complete source batches create checkpoints; detail rows and their checkpoint
-- are committed by one repository transaction.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS training_detail_sync_checkpoint (
    checkpoint_id             uuid PRIMARY KEY,
    user_id                   uuid NOT NULL,
    source_system             text NOT NULL,
    sync_mode                 text NOT NULL,
    bootstrap_completed       boolean NOT NULL,
    source_event_watermark    timestamptz NOT NULL,
    parser_version            text NOT NULL,
    provider_version          text NOT NULL,
    pages_fetched             integer NOT NULL,
    logical_requests          integer NOT NULL,
    attempts_made             integer NOT NULL,
    retries                   integer NOT NULL,
    completed_at              timestamptz NOT NULL,
    CONSTRAINT uq_training_detail_sync_checkpoint_owner
        UNIQUE (checkpoint_id, user_id),
    CONSTRAINT chk_training_detail_sync_source
        CHECK (source_system = 'hevy'),
    CONSTRAINT chk_training_detail_sync_mode
        CHECK (sync_mode IN ('bootstrap', 'incremental')),
    CONSTRAINT chk_training_detail_sync_complete
        CHECK (bootstrap_completed),
    CONSTRAINT chk_training_detail_sync_parser
        CHECK (length(btrim(parser_version)) > 0),
    CONSTRAINT chk_training_detail_sync_provider
        CHECK (length(btrim(provider_version)) > 0),
    CONSTRAINT chk_training_detail_sync_pages
        CHECK (pages_fetched >= 1),
    CONSTRAINT chk_training_detail_sync_requests
        CHECK (logical_requests >= pages_fetched),
    CONSTRAINT chk_training_detail_sync_attempts
        CHECK (attempts_made = logical_requests + retries)
);

CREATE INDEX IF NOT EXISTS ix_training_detail_sync_owner_latest
    ON training_detail_sync_checkpoint (
        user_id,
        source_system,
        source_event_watermark DESC,
        completed_at DESC,
        checkpoint_id DESC
    );

CREATE OR REPLACE FUNCTION prevent_training_detail_sync_regression()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    prior_bootstrap boolean;
    prior_watermark timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            NEW.user_id::text || chr(31) || NEW.source_system || chr(31)
                || 'sync-checkpoint',
            0
        )
    );

    SELECT bool_or(bootstrap_completed), max(source_event_watermark)
      INTO prior_bootstrap, prior_watermark
      FROM training_detail_sync_checkpoint
     WHERE user_id=NEW.user_id AND source_system=NEW.source_system;

    IF prior_bootstrap IS NULL AND NEW.sync_mode <> 'bootstrap' THEN
        RAISE EXCEPTION 'first detailed training sync checkpoint must be bootstrap'
            USING ERRCODE='23514';
    END IF;
    IF COALESCE(prior_bootstrap, false) AND NEW.sync_mode <> 'incremental' THEN
        RAISE EXCEPTION 'later detailed training sync checkpoints must be incremental'
            USING ERRCODE='23514';
    END IF;
    IF COALESCE(prior_bootstrap, false) AND NOT NEW.bootstrap_completed THEN
        RAISE EXCEPTION 'detailed training sync bootstrap state cannot regress'
            USING ERRCODE='23514';
    END IF;
    IF prior_watermark IS NOT NULL
       AND NEW.source_event_watermark < prior_watermark THEN
        RAISE EXCEPTION 'detailed training source watermark cannot regress'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_training_detail_sync_no_regression
    ON training_detail_sync_checkpoint;
CREATE TRIGGER trg_training_detail_sync_no_regression
BEFORE INSERT ON training_detail_sync_checkpoint
FOR EACH ROW EXECUTE FUNCTION prevent_training_detail_sync_regression();

ALTER TABLE training_detail_sync_checkpoint ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS training_detail_sync_owner_select
    ON training_detail_sync_checkpoint;
CREATE POLICY training_detail_sync_owner_select ON training_detail_sync_checkpoint
    FOR SELECT TO authenticated
    USING (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

DROP POLICY IF EXISTS training_detail_sync_owner_insert
    ON training_detail_sync_checkpoint;
CREATE POLICY training_detail_sync_owner_insert ON training_detail_sync_checkpoint
    FOR INSERT TO authenticated
    WITH CHECK (user_id=NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid);

REVOKE ALL ON training_detail_sync_checkpoint FROM authenticated;
GRANT SELECT, INSERT ON training_detail_sync_checkpoint TO authenticated;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND NOT pg_has_role('postgres', 'authenticated', 'member') THEN
        EXECUTE 'GRANT authenticated TO postgres';
    END IF;
END
$$;
