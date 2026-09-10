-- M8: page authority and offering-level nutrition usability are separate.
-- Accepted membership remains complete and immutable; recognized source
-- placeholders/incomplete/unavailable labels carry no fabricated profile.

ALTER TABLE menu_page_offering
    ADD COLUMN IF NOT EXISTS nutrition_source_state text,
    ADD COLUMN IF NOT EXISTS nutrition_snapshot_id uuid
        REFERENCES source_snapshot(snapshot_id);

-- Every pre-0007 accepted membership already has an exact immutable profile.
-- Its source state and label snapshot can therefore be backfilled without
-- inferring anything from mutable menu_offering rows.
UPDATE menu_page_offering AS membership
SET nutrition_source_state = 'profile_available',
    nutrition_snapshot_id = profile.snapshot_id
FROM nutrition_profile AS profile
WHERE membership.profile_id = profile.profile_id
  AND (
      membership.nutrition_source_state IS NULL
      OR membership.nutrition_snapshot_id IS NULL
  );

ALTER TABLE menu_page_offering
    ALTER COLUMN nutrition_source_state SET NOT NULL,
    ALTER COLUMN nutrition_snapshot_id SET NOT NULL,
    ALTER COLUMN profile_id DROP NOT NULL;

ALTER TABLE menu_page_offering
    DROP CONSTRAINT IF EXISTS chk_menu_page_offering_nutrition_state;

ALTER TABLE menu_page_offering
    ADD CONSTRAINT chk_menu_page_offering_nutrition_state CHECK (
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
    );

-- Global source authority remains behind the trusted backend connection.
-- No RLS or authenticated-role access is introduced.
REVOKE ALL PRIVILEGES ON TABLE menu_page_version, menu_page_offering
    FROM authenticated;
