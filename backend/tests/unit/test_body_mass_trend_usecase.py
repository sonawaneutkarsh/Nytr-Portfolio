"""Application/repository tests for the M9 body-mass trend coordination path."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.health.entities import BodyMassSample, SampleDeletion, SyncBatch
from nutrition_agent.domain.health.trend import (
    BodyMassObservation,
    BodyMassTrendStatus,
    InvalidBodyMassTrendTimezone,
)

USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
AS_OF = date(2026, 8, 28)


class _SpyRepository:
    def __init__(self, observations: tuple[BodyMassObservation, ...] = ()) -> None:
        self.observations = observations
        self.calls: list[tuple[UUID, datetime, datetime]] = []

    def list_active(
        self, user_id: UUID, start_inclusive: datetime, end_exclusive: datetime
    ) -> tuple[BodyMassObservation, ...]:
        self.calls.append((user_id, start_inclusive, end_exclusive))
        return self.observations


def _sample(sample_id: int, user_day: int, value: str = "70", *, month: int = 8) -> BodyMassSample:
    measured_at = datetime(2026, month, user_day, 12, tzinfo=UTC)
    return BodyMassSample(
        sample_uuid=UUID(int=sample_id),
        value_kg=Decimal(value),
        sample_start=measured_at,
        sample_end=measured_at,
    )


def _batch(*samples: BodyMassSample, deletions: tuple[SampleDeletion, ...] = ()) -> SyncBatch:
    return SyncBatch(UUID(int=0xF), samples, deletions)


def test_use_case_passes_exact_owner_and_timezone_window_to_repository() -> None:
    repo = _SpyRepository()
    summary = BodyMassTrendUseCase(repo).execute(
        user_id=USER_A,
        as_of_date=date(2026, 3, 8),
        timezone="America/New_York",
    )
    assert summary.status is BodyMassTrendStatus.NO_DATA
    assert repo.calls == [
        (
            USER_A,
            datetime(2026, 2, 9, 5, tzinfo=UTC),
            datetime(2026, 3, 9, 4, tzinfo=UTC),
        )
    ]


def test_invalid_timezone_fails_before_repository_read() -> None:
    repo = _SpyRepository()
    with pytest.raises(InvalidBodyMassTrendTimezone):
        BodyMassTrendUseCase(repo).execute(
            user_id=USER_A,
            as_of_date=AS_OF,
            timezone="invalid/timezone",
        )
    assert repo.calls == []


def test_in_memory_history_read_is_owner_scoped_bounded_and_excludes_tombstones() -> None:
    repo = InMemoryHealthBodyMassRepository()
    repo.apply_batch(
        USER_A,
        _batch(_sample(1, 31, month=7), _sample(2, 20), _sample(3, 28)),
    )
    repo.apply_batch(USER_B, _batch(_sample(4, 27, "99")))
    repo.apply_batch(USER_A, _batch(deletions=(SampleDeletion(UUID(int=2)),)))

    summary = BodyMassTrendUseCase(repo).execute(
        user_id=USER_A,
        as_of_date=AS_OF,
        timezone="UTC",
    )

    assert summary.represented_day_count == 1
    assert summary.latest_measurement_date == AS_OF
    assert summary.status is BodyMassTrendStatus.INSUFFICIENT


def test_tombstoned_observation_does_not_influence_digest_or_result() -> None:
    with_deleted = InMemoryHealthBodyMassRepository()
    active_only = InMemoryHealthBodyMassRepository()
    with_deleted.apply_batch(USER_A, _batch(_sample(1, 27, "99"), _sample(2, 28, "70")))
    with_deleted.apply_batch(USER_A, _batch(deletions=(SampleDeletion(UUID(int=1)),)))
    active_only.apply_batch(USER_A, _batch(_sample(2, 28, "70")))

    first = BodyMassTrendUseCase(with_deleted).execute(
        user_id=USER_A, as_of_date=AS_OF, timezone="UTC"
    )
    second = BodyMassTrendUseCase(active_only).execute(
        user_id=USER_A, as_of_date=AS_OF, timezone="UTC"
    )
    assert first == second
