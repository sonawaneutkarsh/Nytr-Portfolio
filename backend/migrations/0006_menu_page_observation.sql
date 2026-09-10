-- M8: accepted page versions are immutable observations, not canonical-content
-- identities. source_snapshot remains content-deduplicated, while a later run
-- may accept the same canonical bytes again for the same source page.

ALTER TABLE menu_page_version
    DROP CONSTRAINT IF EXISTS uq_menu_page_version_source;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_menu_page_version_run_page'
          AND conrelid = 'menu_page_version'::regclass
    ) THEN
        ALTER TABLE menu_page_version
            ADD CONSTRAINT uq_menu_page_version_run_page UNIQUE (
                ingestion_run_id,
                service_date,
                meal_period,
                campus_id
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_menu_page_version_authoritative
    ON menu_page_version (
        service_date,
        meal_period,
        campus_id,
        accepted_at DESC,
        page_version_id DESC
    );

-- These remain trusted-backend global source tables. No RLS or authenticated
-- privileges are introduced by this observation-ordering correction.
