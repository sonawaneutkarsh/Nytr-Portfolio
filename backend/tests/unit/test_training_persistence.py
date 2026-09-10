"""In-memory and application regression tests for immutable workouts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from nutrition_agent.application.training_sync import (
    TrainingAccepted,
    TrainingRejected,
    TrainingSessionSyncUseCase,
    TrainingSyncDeps,
)
from nutrition_agent.db.in_memory_repos import InMemoryTrainingSessionRepository
from nutrition_agent.domain.training import (
    TrainingSessionDeletion,
    TrainingSessionObservation,
    TrainingSourceSystem,
    TrainingSyncBatch,
)

USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
T0 = datetime(2026, 9, 3, 12, tzinfo=UTC)
SOURCE_ONE = UUID(int=1)


class _Clock:
    def now(self) -> datetime:
        return T0


def _observation(source_id: UUID = SOURCE_ONE, *, energy: str | None = "400"):
    return TrainingSessionObservation(
        source_system=TrainingSourceSystem.HEALTHKIT,
        source_record_id=str(source_id),
        activity_type="37",
        started_at=T0 - timedelta(hours=1),
        ended_at=T0,
        active_duration_seconds=Decimal("3600"),
        active_energy_kcal=Decimal(energy) if energy is not None else None,
        timezone_identifier="America/New_York",
        source_name="Apple Watch",
        source_bundle_id="com.apple.health",
        source_revision="26.0",
    )


def _batch(*, added=(), deletions=()) -> TrainingSyncBatch:
    return TrainingSyncBatch(uuid4(), tuple(added), tuple(deletions))


def _deletion(source_id: UUID = SOURCE_ONE) -> TrainingSessionDeletion:
    return TrainingSessionDeletion(TrainingSourceSystem.HEALTHKIT, str(source_id))


def test_insert_replay_is_idempotent_and_never_overwrites_observation() -> None:
    repo = InMemoryTrainingSessionRepository(clock=_Clock().now, ids=lambda: UUID(int=99))
    first = repo.apply_batch(USER_A, _batch(added=[_observation()]))
    conflicting_replay = _observation(energy="999")
    second = repo.apply_batch(USER_A, _batch(added=[conflicting_replay]))
    key = (USER_A, TrainingSourceSystem.HEALTHKIT, str(UUID(int=1)))
    assert (first.accepted_added, second.duplicate_added) == (1, 1)
    assert repo.rows[key].session_id == UUID(int=99)
    assert repo.rows[key].active_energy_kcal == Decimal("400")


def test_same_source_identity_is_owner_scoped() -> None:
    repo = InMemoryTrainingSessionRepository()
    assert repo.apply_batch(USER_A, _batch(added=[_observation()])).accepted_added == 1
    assert repo.apply_batch(USER_B, _batch(added=[_observation()])).accepted_added == 1
    assert len(repo.rows) == 2


def test_deletion_before_add_is_durable_and_prevents_resurrection() -> None:
    repo = InMemoryTrainingSessionRepository(clock=_Clock().now)
    first = repo.apply_batch(USER_A, _batch(deletions=[_deletion()]))
    duplicate = repo.apply_batch(USER_A, _batch(deletions=[_deletion()]))
    late = repo.apply_batch(USER_A, _batch(added=[_observation()]))
    assert first.applied_deletions == 1
    assert duplicate.duplicate_deletions == 1
    assert late.duplicate_added == 1
    assert repo.rows == {}
    assert len(repo.tombstones) == 1


def test_deletion_after_add_tombstones_without_rewriting_values() -> None:
    repo = InMemoryTrainingSessionRepository(clock=_Clock().now)
    repo.apply_batch(USER_A, _batch(added=[_observation()]))
    outcome = repo.apply_batch(USER_A, _batch(deletions=[_deletion()]))
    row = next(iter(repo.rows.values()))
    assert outcome.applied_deletions == 1
    assert row.tombstoned_at == T0
    assert row.active_energy_kcal == Decimal("400")
    assert repo.list_active(USER_A, T0 - timedelta(days=1), T0 + timedelta(days=1)) == ()


def test_add_and_delete_same_batch_finishes_tombstoned() -> None:
    repo = InMemoryTrainingSessionRepository(clock=_Clock().now)
    outcome = repo.apply_batch(USER_A, _batch(added=[_observation()], deletions=[_deletion()]))
    assert (outcome.accepted_added, outcome.applied_deletions) == (1, 1)
    assert next(iter(repo.rows.values())).tombstoned_at == T0


def test_active_listing_is_owner_scoped_range_bounded_and_deterministic() -> None:
    repo = InMemoryTrainingSessionRepository()
    later = _observation(UUID(int=2))
    earlier = replace(
        later,
        source_record_id=str(UUID(int=1)),
        started_at=T0 - timedelta(hours=2),
    )
    repo.apply_batch(USER_A, _batch(added=[later, earlier]))
    repo.apply_batch(USER_B, _batch(added=[_observation(UUID(int=3))]))
    found = repo.list_active(USER_A, T0 - timedelta(days=1), T0 + timedelta(seconds=1))
    assert tuple(item.source_record_id for item in found) == (str(UUID(int=1)), str(UUID(int=2)))


def test_use_case_rejects_invalid_batch_without_repository_mutation() -> None:
    repo = InMemoryTrainingSessionRepository()
    use_case = TrainingSessionSyncUseCase(TrainingSyncDeps(repo, _Clock()))
    result = use_case.submit(USER_A, str(uuid4()), [{"source_system": "hevy"}], [])
    assert isinstance(result, TrainingRejected)
    assert repo.rows == {} and repo.tombstones == {}


def test_use_case_accepts_valid_raw_payload_with_exact_decimal() -> None:
    repo = InMemoryTrainingSessionRepository(clock=_Clock().now)
    use_case = TrainingSessionSyncUseCase(TrainingSyncDeps(repo, _Clock()))
    raw = {
        "source_system": "healthkit",
        "source_record_id": str(UUID(int=1)),
        "activity_type": "37",
        "started_at": "2026-09-03T10:00:00+00:00",
        "ended_at": "2026-09-03T11:00:00+00:00",
        "active_duration_seconds": "3600.000",
        "active_energy_kcal": "412.750",
    }
    result = use_case.submit(USER_A, str(uuid4()), [raw], [])
    assert isinstance(result, TrainingAccepted)
    row = next(iter(repo.rows.values()))
    assert row.active_duration_seconds == Decimal("3600.000")
    assert row.active_energy_kcal == Decimal("412.750")
