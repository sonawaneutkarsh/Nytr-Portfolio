from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from nutrition_agent.application.ports import (
    AcceptedMenuPageKey,
    AcceptedMenuPageObservation,
)
from nutrition_agent.db.sql_repos import (
    SqlMenuPageCoverageRepository,
    SqlStacksRefreshLease,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; refresh SQL tests require scratch Postgres",
)


def _insert_empty_accepted_page(
    service_date: date,
    period: MealPeriod,
    *,
    accepted_at: datetime | None = None,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    run_id = uuid4()
    snapshot_id = uuid4()
    page_id = uuid4()
    now = accepted_at or datetime(2095, 1, 1, 12, tzinfo=UTC)
    sha = uuid4().hex + uuid4().hex
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stacks_ingestion_run (
                run_id, status, mode, params, config_fingerprint,
                started_at, finished_at)
            VALUES (%s,'persisted','fixture','{}','refresh-test',%s,%s)
            """,
            (str(run_id), now, now),
        )
        cur.execute(
            """
            INSERT INTO source_snapshot (
                snapshot_id, source_system, source_url, http_method,
                request_params, http_status, content_sha256, byte_size,
                fetched_at, parser_version, ingestion_run_id, storage_path)
            VALUES (%s,'institutional_menu','https://fixture.invalid/menu','POST',
                    '{}',200,%s,1,%s,'refresh-test',%s,%s)
            """,
            (str(snapshot_id), sha, now, str(run_id), f"fixture/{sha}.html"),
        )
        cur.execute(
            """
            INSERT INTO menu_page_version (
                page_version_id, service_date, meal_period, campus_id,
                snapshot_id, ingestion_run_id, validation_state,
                offering_count, accepted_at, parser_version)
            VALUES (%s,%s,%s,50,%s,%s,'validated_empty',0,%s,'refresh-test')
            """,
            (
                str(page_id),
                service_date,
                period.value,
                str(snapshot_id),
                str(run_id),
                now,
            ),
        )


def test_coverage_reads_only_accepted_pages_inside_exact_bounds() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    target_date = date(2095, 1, 10)
    _insert_empty_accepted_page(target_date, MealPeriod.LUNCH)
    latest = datetime(2095, 1, 2, 14, tzinfo=UTC)
    _insert_empty_accepted_page(target_date, MealPeriod.LUNCH, accepted_at=latest)
    _insert_empty_accepted_page(date(2095, 2, 1), MealPeriod.DINNER)

    pages = SqlMenuPageCoverageRepository(DATABASE_URL).accepted_pages(
        campus_id=50,
        start_date=target_date,
        end_date=target_date,
    )

    assert pages == (
        AcceptedMenuPageObservation(
            AcceptedMenuPageKey(target_date, MealPeriod.LUNCH, 50),
            latest,
        ),
    )


def test_refresh_lease_prevents_overlap_and_releases_on_context_exit() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)

    with SqlStacksRefreshLease(DATABASE_URL) as first:
        assert first.acquired is True
        with SqlStacksRefreshLease(DATABASE_URL) as overlapping:
            assert overlapping.acquired is False

    with SqlStacksRefreshLease(DATABASE_URL) as after_release:
        assert after_release.acquired is True


def test_coverage_rejects_inverted_bounds() -> None:
    assert DATABASE_URL is not None
    with pytest.raises(ValueError, match="end_date"):
        SqlMenuPageCoverageRepository(DATABASE_URL).accepted_pages(
            campus_id=50,
            start_date=date(2095, 1, 2),
            end_date=date(2095, 1, 1),
        )
