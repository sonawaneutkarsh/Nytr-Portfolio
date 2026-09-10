"""Application proofs for bounded, checkpointed official Hevy synchronization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.application.hevy_sync import SyncHevyDetailedTrainingUseCase
from nutrition_agent.application.ports import (
    DetailedTrainingSourceError,
    DetailedTrainingSourceFailureKind,
)
from nutrition_agent.db.in_memory_repos import InMemoryDetailedTrainingRepository
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingImportOutcome,
    DetailedTrainingSourceFetch,
    DetailedTrainingSyncCheckpoint,
    DetailedTrainingSyncMode,
    DetailedTrainingSyncStatus,
)
from nutrition_agent.infrastructure.hevy_detail_source import HevyFixtureProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hevy"
USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


class _Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self, values: Sequence[UUID]) -> None:
        self._values = iter(values)

    def new_id(self) -> UUID:
        return next(self._values)


class _Source:
    def __init__(
        self,
        initial: DetailedTrainingSourceFetch,
        changes: Sequence[DetailedTrainingSourceFetch] = (),
        *,
        configured: bool = True,
    ) -> None:
        self._initial = initial
        self._changes = iter(changes)
        self.is_configured = configured
        self.initial_calls = 0
        self.change_since: list[datetime] = []

    def load_initial(self) -> DetailedTrainingSourceFetch:
        self.initial_calls += 1
        return self._initial

    def load_changes(self, since: datetime) -> DetailedTrainingSourceFetch:
        self.change_since.append(since)
        return next(self._changes)


def _batch(*names: str) -> DetailedTrainingImportBatch:
    return HevyFixtureProvider(
        tuple((FIXTURES / name).read_bytes() for name in names)
    ).load_changes(None)


def _fetch(
    batch: DetailedTrainingImportBatch,
    watermark: datetime,
    *,
    complete: bool = True,
    pages: int = 1,
) -> DetailedTrainingSourceFetch:
    return DetailedTrainingSourceFetch(
        batch=replace(batch, complete=complete),
        pages_fetched=pages,
        has_more=not complete,
        source_event_watermark=watermark,
        logical_requests=pages,
        attempts_made=pages,
        retries=0,
        parser_version="hevy-public-api.v1",
        provider_version="hevy-api-provider.v1",
    )


def _use_case(
    source: _Source,
    repository: InMemoryDetailedTrainingRepository,
    *,
    checkpoint_ids: Sequence[UUID] = (UUID(int=101), UUID(int=102), UUID(int=103)),
) -> SyncHevyDetailedTrainingUseCase:
    return SyncHevyDetailedTrainingUseCase(
        source=source,
        repository=repository,
        clock=_Clock(),
        ids=_Ids(checkpoint_ids),
    )


def test_bootstrap_persists_complete_batch_and_checkpoint_atomically() -> None:
    initial = _batch("events_page_1.json", "events_page_2.json")
    watermark = max(
        *(item.source_updated_at for item in initial.sessions if item.source_updated_at),
        *(item.removed_at for item in initial.deletions),
    )
    source = _Source(_fetch(initial, watermark, pages=2))
    repository = InMemoryDetailedTrainingRepository()

    outcome = _use_case(source, repository).execute(user_id=USER_A)

    assert outcome.status is DetailedTrainingSyncStatus.SYNCED
    assert outcome.mode is DetailedTrainingSyncMode.BOOTSTRAP
    assert (outcome.sessions_created, outcome.deletions_recorded) == (2, 1)
    assert outcome.checkpoint_advanced is True
    assert source.initial_calls == 1 and source.change_since == []
    checkpoint = repository.latest_sync_checkpoint(USER_A, initial.sessions[0].source_system)
    assert checkpoint is not None
    assert checkpoint.source_event_watermark == watermark


def test_incremental_uses_one_second_overlap_and_replay_is_idempotent() -> None:
    initial_batch = _batch("events_updated_revision.json")
    watermark = initial_batch.sessions[0].source_updated_at
    assert watermark is not None
    replay = _fetch(initial_batch, watermark)
    source = _Source(replay, (replay,))
    repository = InMemoryDetailedTrainingRepository()
    use_case = _use_case(source, repository)

    first = use_case.execute(user_id=USER_A)
    second = use_case.execute(user_id=USER_A)

    assert first.sessions_created == 1
    assert second.mode is DetailedTrainingSyncMode.INCREMENTAL
    assert second.events_replayed == 1
    assert second.sessions_created == second.revisions_appended == 0
    assert source.change_since == [watermark - timedelta(seconds=1)]
    assert len(repository.revisions) == 1
    assert len(repository.sync_checkpoints) == 2


def test_incremental_update_appends_revision_and_deletion_is_permanent() -> None:
    original = _batch("events_page_1.json", "events_page_2.json").sessions[0]
    updated = _batch("events_updated_revision.json").sessions[0]
    original_updated_at = original.source_updated_at
    updated_at = updated.source_updated_at
    assert original_updated_at is not None and updated_at is not None
    deletion = replace(
        _batch("events_page_1.json", "events_page_2.json").deletions[0],
        source_session_id=original.source_session_id,
    )
    source = _Source(
        _fetch(DetailedTrainingImportBatch((original,), (), True), original_updated_at),
        (
            _fetch(
                DetailedTrainingImportBatch((updated,), (), True),
                updated_at,
            ),
            _fetch(
                DetailedTrainingImportBatch((), (deletion,), True),
                deletion.removed_at,
            ),
            _fetch(
                DetailedTrainingImportBatch((updated,), (), True),
                updated_at,
            ),
        ),
    )
    repository = InMemoryDetailedTrainingRepository()
    use_case = _use_case(source, repository, checkpoint_ids=tuple(UUID(int=i) for i in range(1, 6)))

    use_case.execute(user_id=USER_A)
    changed = use_case.execute(user_id=USER_A)
    removed = use_case.execute(user_id=USER_A)
    blocked = use_case.execute(user_id=USER_A)

    assert changed.revisions_appended == 1
    assert removed.deletions_recorded == 1
    assert blocked.tombstone_blocked == 1
    assert repository.list_latest(USER_A, 50) == ()


def test_cap_reached_reports_incomplete_without_import_or_checkpoint() -> None:
    batch = _batch("events_updated_revision.json")
    source = _Source(_fetch(batch, NOW, complete=False))
    repository = InMemoryDetailedTrainingRepository()

    outcome = _use_case(source, repository).execute(user_id=USER_A)

    assert outcome.status is DetailedTrainingSyncStatus.INCOMPLETE
    assert outcome.has_more is True
    assert outcome.checkpoint_advanced is False
    assert repository.revisions == {}
    assert repository.sync_checkpoints == []


def test_provider_failure_and_unconfigured_source_never_advance_state() -> None:
    batch = _batch("events_updated_revision.json")

    class _FailingSource(_Source):
        def load_initial(self) -> DetailedTrainingSourceFetch:
            raise DetailedTrainingSourceError(
                DetailedTrainingSourceFailureKind.TIMEOUT,
                "provider timeout",
                attempts_made=2,
                retries=1,
            )

    repository = InMemoryDetailedTrainingRepository()
    with pytest.raises(DetailedTrainingSourceError):
        _use_case(_FailingSource(_fetch(batch, NOW)), repository).execute(user_id=USER_A)
    assert repository.sync_checkpoints == []

    unconfigured = _Source(_fetch(batch, NOW), configured=False)
    with pytest.raises(DetailedTrainingSourceError) as error:
        _use_case(unconfigured, repository).execute(user_id=USER_A)
    assert error.value.kind is DetailedTrainingSourceFailureKind.NOT_CONFIGURED
    assert unconfigured.initial_calls == 0


def test_persistence_failure_rolls_back_detail_and_checkpoint() -> None:
    class _FailingRepository(InMemoryDetailedTrainingRepository):
        def apply_sync(
            self,
            user_id: UUID,
            batch: DetailedTrainingImportBatch,
            ingested_at: datetime,
            checkpoint: DetailedTrainingSyncCheckpoint,
        ) -> DetailedTrainingImportOutcome:
            del user_id, batch, ingested_at, checkpoint
            raise RuntimeError("injected persistence failure")

    initial = _batch("events_updated_revision.json")
    source = _Source(_fetch(initial, NOW))
    repository = _FailingRepository()

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        _use_case(source, repository).execute(user_id=USER_A)

    assert repository.revisions == {}
    assert repository.sync_checkpoints == []


def test_checkpoint_identity_conflict_rolls_back_import() -> None:
    initial = _batch("events_updated_revision.json")
    repository = InMemoryDetailedTrainingRepository()
    first_source = _Source(_fetch(initial, NOW))
    _use_case(first_source, repository, checkpoint_ids=(UUID(int=99),)).execute(user_id=USER_A)
    newer = replace(
        initial.sessions[0],
        source_revision="2026-09-04T13:00:00+00:00",
        source_updated_at=datetime(2026, 9, 4, 13, tzinfo=UTC),
        source_payload_sha256="f" * 64,
    )
    next_source = _Source(
        _fetch(initial, NOW),
        (_fetch(DetailedTrainingImportBatch((newer,), (), True), NOW + timedelta(hours=1)),),
    )
    with pytest.raises(ValueError, match="checkpoint identity"):
        _use_case(next_source, repository, checkpoint_ids=(UUID(int=99),)).execute(user_id=USER_A)
    assert len(repository.revisions) == 1
    assert len(repository.sync_checkpoints) == 1


def test_empty_complete_bootstrap_advances_checkpoint_without_invented_sessions() -> None:
    empty = DetailedTrainingImportBatch((), (), True)
    source = _Source(_fetch(empty, NOW))
    repository = InMemoryDetailedTrainingRepository()

    outcome = _use_case(source, repository).execute(user_id=USER_A)

    assert outcome.status is DetailedTrainingSyncStatus.SYNCED
    assert outcome.sessions_created == outcome.deletions_recorded == 0
    assert outcome.checkpoint_advanced is True
    assert repository.list_latest(USER_A, 50) == ()


def test_sync_state_and_detail_rows_are_owner_scoped() -> None:
    batch = _batch("events_updated_revision.json")
    source = _Source(_fetch(batch, NOW))
    repository = InMemoryDetailedTrainingRepository()
    use_case = _use_case(source, repository, checkpoint_ids=(UUID(int=1), UUID(int=2)))

    use_case.execute(user_id=USER_A)
    use_case.execute(user_id=USER_B)

    source_system = batch.sessions[0].source_system
    assert repository.latest_sync_checkpoint(USER_A, source_system).user_id == USER_A  # type: ignore[union-attr]
    assert repository.latest_sync_checkpoint(USER_B, source_system).user_id == USER_B  # type: ignore[union-attr]
    assert len(repository.list_latest(USER_A, 50)) == 1
    assert len(repository.list_latest(USER_B, 50)) == 1
