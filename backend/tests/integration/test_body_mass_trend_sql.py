"""PostgreSQL/RLS proof for the M9 owner-scoped body-mass history read."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.db.sql_repos import SqlHealthBodyMassRepository
from nutrition_agent.domain.health.entities import BodyMassSample, SampleDeletion, SyncBatch
from nutrition_agent.domain.health.trend import (
    BodyMassTrendStatus,
    aggregate_daily_body_mass,
    calculate_body_mass_trend,
)
from tests.migration_helpers import MIGRATIONS, apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; requires scratch Postgres",
)

USER_A = UUID("00000000-0000-0000-0000-000000009a01")
USER_B = UUID("00000000-0000-0000-0000-000000009b02")
AS_OF = date(2026, 8, 28)


def _repo() -> SqlHealthBodyMassRepository:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    _cleanup()
    return SqlHealthBodyMassRepository(DATABASE_URL)


def _cleanup() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "DELETE FROM health_body_mass_sample WHERE user_id IN (%s, %s)",
            (str(USER_A), str(USER_B)),
        )


def _sample(sample_id: int, measured_at: datetime, value: str = "70") -> BodyMassSample:
    return BodyMassSample(
        sample_uuid=UUID(int=sample_id),
        value_kg=Decimal(value),
        sample_start=measured_at,
        sample_end=measured_at,
    )


def _batch(*samples: BodyMassSample, deletions: tuple[SampleDeletion, ...] = ()) -> SyncBatch:
    return SyncBatch(UUID(int=0xF), samples, deletions)


def test_sql_history_read_is_owner_scoped_bounded_and_excludes_tombstones() -> None:
    repo = _repo()
    repo.apply_batch(
        USER_A,
        _batch(
            _sample(1, datetime(2026, 7, 31, 12, tzinfo=UTC), "60"),
            _sample(2, datetime(2026, 8, 10, 12, tzinfo=UTC), "70"),
            _sample(3, datetime(2026, 8, 15, 12, tzinfo=UTC), "90"),
            _sample(4, datetime(2026, 8, 28, 12, tzinfo=UTC), "72"),
        ),
    )
    repo.apply_batch(USER_A, _batch(deletions=(SampleDeletion(UUID(int=3)),)))
    repo.apply_batch(
        USER_B,
        _batch(_sample(5, datetime(2026, 8, 27, 12, tzinfo=UTC), "99")),
    )

    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 29, tzinfo=UTC)
    observations_a = repo.list_active(USER_A, start, end)
    observations_b = repo.list_active(USER_B, start, end)

    assert tuple(item.sample_uuid for item in observations_a) == (UUID(int=2), UUID(int=4))
    assert tuple(item.value_kg for item in observations_a) == (Decimal("70.000"), Decimal("72.000"))
    assert tuple(item.sample_uuid for item in observations_b) == (UUID(int=5),)


def test_sql_history_rls_hides_a_when_session_subject_is_b() -> None:
    repo = _repo()
    repo.apply_batch(
        USER_A,
        _batch(_sample(1, datetime(2026, 8, 28, 12, tzinfo=UTC))),
    )
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(USER_B),))
        cur.execute(
            "SELECT COUNT(*) FROM health_body_mass_sample WHERE user_id=%s",
            (str(USER_A),),
        )
        assert cur.fetchone()[0] == 0


def test_sql_timestamps_are_grouped_in_python_and_result_is_order_invariant() -> None:
    repo = _repo()
    repo.apply_batch(
        USER_A,
        _batch(
            _sample(2, datetime(2026, 8, 28, 4, 30, tzinfo=UTC), "72"),
            _sample(1, datetime(2026, 8, 28, 3, 30, tzinfo=UTC), "70"),
        ),
    )
    observations = repo.list_active(
        USER_A,
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 29, tzinfo=UTC),
    )
    daily = aggregate_daily_body_mass(observations, "America/New_York")
    assert tuple(point.local_date for point in daily) == (date(2026, 8, 27), date(2026, 8, 28))

    first = calculate_body_mass_trend(
        user_id=USER_A,
        observations=observations,
        as_of_date=AS_OF,
        timezone="America/New_York",
    )
    second = calculate_body_mass_trend(
        user_id=USER_A,
        observations=tuple(reversed(observations)),
        as_of_date=AS_OF,
        timezone="America/New_York",
    )
    assert first == second


def test_sql_repository_drives_application_use_case_without_persisting_summary() -> None:
    repo = _repo()
    repo.apply_batch(
        USER_A,
        _batch(
            *(
                _sample(
                    index + 1,
                    datetime(2026, 8, day, 12, tzinfo=UTC),
                    str(69 + index),
                )
                for index, day in enumerate((8, 10, 12, 14, 16, 22, 28))
            )
        ),
    )
    summary = BodyMassTrendUseCase(repo).execute(
        user_id=USER_A,
        as_of_date=AS_OF,
        timezone="UTC",
    )
    assert summary.status is BodyMassTrendStatus.READY
    assert summary.represented_day_count == 7
    assert summary.coverage_span_days == 20

    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema='public' AND table_name LIKE %s",
            ("%trend%",),
        )
        assert cur.fetchall() == []


def test_existing_active_index_supports_m9_and_no_trend_migration_exists() -> None:
    _repo()
    assert DATABASE_URL is not None
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT indexdef FROM pg_indexes"
            " WHERE schemaname='public' AND indexname='ix_hbm_user_active'"
        )
        definition = str(cur.fetchone()[0])
        assert "user_id" in definition and "sample_start DESC" in definition
        assert "tombstoned_at IS NULL" in definition
    assert all("trend" not in path.name for path in MIGRATIONS.glob("*.sql"))
