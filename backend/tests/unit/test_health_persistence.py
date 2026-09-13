"""Offline tests for body-mass persistence semantics and the sync use case.

The in-memory repository mirrors migration 0002 / SQL repo semantics exactly,
so these tests pin the same idempotency, tombstone, and no-resurrection
behavior the DB enforces (DB-gated integration tests prove the SQL side).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from nutrition_agent.application.health_sync import (
    Accepted,
    HealthBodyMassSyncUseCase,
    HealthSyncDeps,
    Rejected,
)
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.health.entities import (
    BodyMassSample,
    SampleDeletion,
    SyncBatch,
)

USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
T0 = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _sample(uuid_int: int = 1, value: str = "68.039") -> BodyMassSample:
    return BodyMassSample(
        sample_uuid=UUID(int=uuid_int),
        value_kg=Decimal(value),
        sample_start=T0,
        sample_end=T0,
        source_name=None,
        source_bundle_id=None,
    )


class _FixedClock:
    def __init__(self) -> None:
        self.value = T0

    def now(self) -> datetime:
        return self.value


def _use_case() -> tuple[HealthBodyMassSyncUseCase, InMemoryHealthBodyMassRepository]:
    repo = InMemoryHealthBodyMassRepository()
    return HealthBodyMassSyncUseCase(HealthSyncDeps(repo, _FixedClock())), repo


def _batch(added=(), deleted=(), batch_id: UUID | None = None) -> SyncBatch:
    return SyncBatch(
        client_batch_id=batch_id or uuid4(),
        added=tuple(added),
        deletions=tuple(deleted),
    )


# ---------------------------------------------------------------------------
# Repository semantics (mirror of SQL behavior)
# ---------------------------------------------------------------------------


def test_first_insert_is_accepted_second_is_duplicate() -> None:
    repo = InMemoryHealthBodyMassRepository()
    batch = _batch(added=[_sample()])
    first = repo.apply_batch(USER_A, batch)
    second = repo.apply_batch(USER_A, batch)
    assert (first.accepted_added, first.duplicate_added) == (1, 0)
    assert (second.accepted_added, second.duplicate_added) == (0, 1)


def test_same_identity_different_users_are_distinct_rows() -> None:
    repo = InMemoryHealthBodyMassRepository()
    outcome_a = repo.apply_batch(USER_A, _batch(added=[_sample()]))
    outcome_b = repo.apply_batch(USER_B, _batch(added=[_sample()]))
    assert (outcome_a.accepted_added, outcome_b.accepted_added) == (1, 1)
    assert len(repo.rows) == 2


def test_add_never_overwrites_existing_measurement() -> None:
    repo = InMemoryHealthBodyMassRepository()
    repo.apply_batch(USER_A, _batch(added=[_sample(value="68.039")]))
    repo.apply_batch(USER_A, _batch(added=[_sample(value="99.999")]))
    row = repo.rows[(USER_A, UUID(int=1))]
    assert str(row.value_kg) == "68.039"  # first add wins; no silent overwrite


def test_deletion_of_unknown_sample_creates_tombstone_only_row() -> None:
    repo = InMemoryHealthBodyMassRepository()
    outcome = repo.apply_batch(USER_A, _batch(deleted=[SampleDeletion(UUID(int=7))]))
    assert outcome.applied_deletions == 1
    row = repo.rows[(USER_A, UUID(int=7))]
    assert row.value_kg is None and row.sample_start is None and row.sample_end is None
    assert row.tombstoned_at is not None


def test_late_add_after_tombstone_does_not_resurrect() -> None:
    repo = InMemoryHealthBodyMassRepository()
    repo.apply_batch(USER_A, _batch(deleted=[SampleDeletion(UUID(int=7))]))
    outcome = repo.apply_batch(USER_A, _batch(added=[_sample(uuid_int=7)]))
    assert outcome.accepted_added == 0 and outcome.duplicate_added == 1
    row = repo.rows[(USER_A, UUID(int=7))]
    assert row.tombstoned_at is not None
    assert row.value_kg is None  # still a tombstone-only row


def test_duplicate_deletion_keeps_first_tombstoned_at() -> None:
    repo = InMemoryHealthBodyMassRepository(clock=_FixedClock().now)
    repo.apply_batch(USER_A, _batch(deleted=[SampleDeletion(UUID(int=7))]))
    first_ts = repo.rows[(USER_A, UUID(int=7))].tombstoned_at
    repo.clock = lambda: datetime(2027, 1, 1, tzinfo=UTC)
    outcome = repo.apply_batch(USER_A, _batch(deleted=[SampleDeletion(UUID(int=7))]))
    assert outcome.duplicate_deletions == 1
    assert repo.rows[(USER_A, UUID(int=7))].tombstoned_at == first_ts


def test_deletion_after_add_tombstones_and_retains_value() -> None:
    repo = InMemoryHealthBodyMassRepository()
    repo.apply_batch(USER_A, _batch(added=[_sample()]))
    outcome = repo.apply_batch(USER_A, _batch(deleted=[SampleDeletion(UUID(int=1))]))
    assert outcome.applied_deletions == 1
    row = repo.rows[(USER_A, UUID(int=1))]
    assert row.tombstoned_at is not None
    assert str(row.value_kg) == "68.039"  # measurement retained for history


def test_status_summary_counts_and_latest() -> None:
    repo = InMemoryHealthBodyMassRepository()
    older = BodyMassSample(
        sample_uuid=UUID(int=1),
        value_kg=Decimal("66"),
        sample_start=datetime(2026, 8, 20, tzinfo=UTC),
        sample_end=datetime(2026, 8, 20, tzinfo=UTC),
        source_name=None,
        source_bundle_id=None,
    )
    newer = _sample(uuid_int=2)
    repo.apply_batch(USER_A, _batch(added=[older, newer]))
    status = repo.status_summary(USER_A)
    assert status.record_count == 2
    assert status.tombstone_count == 0
    assert status.latest_sample is not None
    assert status.latest_sample.sample_uuid == UUID(int=2)
    assert USER_B not in {uid for uid, _ in repo.rows}


def test_status_counts_tombstones_separately() -> None:
    repo = InMemoryHealthBodyMassRepository()
    repo.apply_batch(
        USER_A,
        _batch(added=[_sample()], deleted=[SampleDeletion(UUID(int=9))]),
    )
    status = repo.status_summary(USER_A)
    assert status.record_count == 1
    assert status.tombstone_count == 1
    assert status.last_ingested_at is not None


def test_status_for_unknown_user_is_empty_not_error() -> None:
    repo = InMemoryHealthBodyMassRepository()
    status = repo.status_summary(USER_B)
    assert (status.record_count, status.tombstone_count) == (0, 0)
    assert status.latest_sample is None and status.last_ingested_at is None


# ---------------------------------------------------------------------------
# Use case orchestration
# ---------------------------------------------------------------------------


def test_use_case_rejects_invalid_batch_without_touching_repository() -> None:
    use_case, repo = _use_case()
    result = use_case.submit(USER_A, str(uuid4()), [{"sample_uuid": "bogus"}], [])
    assert isinstance(result, Rejected)
    assert repo.rows == {}


def test_use_case_accepts_valid_batch_and_returns_outcome() -> None:
    use_case, repo = _use_case()
    raw = {
        "sample_uuid": "00000000-0000-0000-0000-00000000000a",
        "value": "70.5",
        "sample_start": "2026-08-21T07:12:00+00:00",
        "sample_end": "2026-08-21T07:12:00+00:00",
    }
    result = use_case.submit(USER_A, str(uuid4()), [raw], [])
    assert isinstance(result, Accepted)
    assert result.outcome.accepted_added == 1
    assert repo.rows[(USER_A, UUID(int=0xA))].value_kg is not None


def test_use_case_replay_is_fully_idempotent() -> None:
    use_case, repo = _use_case()
    raw = {
        "sample_uuid": "00000000-0000-0000-0000-00000000000a",
        "value": "70.5",
        "sample_start": "2026-08-21T07:12:00+00:00",
        "sample_end": "2026-08-21T07:12:00+00:00",
    }
    batch_id = str(uuid4())
    first = use_case.submit(USER_A, batch_id, [raw], [])
    second = use_case.submit(USER_A, batch_id, [raw], [])
    third = use_case.submit(USER_A, batch_id, [], [{"sample_uuid": raw["sample_uuid"]}])
    assert isinstance(first, Accepted) and isinstance(second, Accepted)
    assert isinstance(third, Accepted)
    assert (first.outcome.duplicate_added, second.outcome.duplicate_added) == (0, 1)
    assert third.outcome.applied_deletions == 1
    assert len(repo.rows) == 1
