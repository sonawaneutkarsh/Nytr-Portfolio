"""Use-case and in-memory persistence proofs for detailed training revisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.application.detailed_training import (
    GetDetailedTrainingSessionUseCase,
    ImportDetailedTrainingSessionsUseCase,
    IncompleteDetailedTrainingImportError,
    ListDetailedTrainingSessionsUseCase,
)
from nutrition_agent.application.ports import DetailedTrainingRevisionConflictError
from nutrition_agent.db.in_memory_repos import InMemoryDetailedTrainingRepository
from nutrition_agent.domain.training.detail import DetailedTrainingImportBatch
from nutrition_agent.infrastructure.hevy_detail_source import HevyFixtureProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hevy"
USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _provider(*names: str) -> HevyFixtureProvider:
    return HevyFixtureProvider(tuple((FIXTURES / name).read_bytes() for name in names))


def _initial_batch() -> DetailedTrainingImportBatch:
    return _provider("events_page_1.json", "events_page_2.json").load_changes(None)


def test_import_replay_is_idempotent_and_returns_latest_summaries() -> None:
    ids = iter((UUID(int=1), UUID(int=2)))
    repo = InMemoryDetailedTrainingRepository(ids=lambda: next(ids))
    use_case = ImportDetailedTrainingSessionsUseCase(
        _provider("events_page_1.json", "events_page_2.json"), repo, _Clock()
    )
    first = use_case.execute(user_id=USER_A)
    second = use_case.execute(user_id=USER_A)
    summaries = ListDetailedTrainingSessionsUseCase(repo).execute(user_id=USER_A)
    assert (first.accepted_revisions, second.duplicate_revisions) == (2, 2)
    assert tuple(item.title for item in summaries) == ("Push Day", "Lower Day")
    assert summaries[0].exercise_count == 6
    assert summaries[0].set_count == 9


def test_source_update_appends_revision_and_latest_projection_changes() -> None:
    ids = iter((UUID(int=1), UUID(int=2), UUID(int=3)))
    repo = InMemoryDetailedTrainingRepository(ids=lambda: next(ids))
    repo.apply_import(USER_A, _initial_batch(), NOW)
    updated_batch = _provider("events_updated_revision.json").load_changes(None)
    outcome = repo.apply_import(USER_A, updated_batch, NOW)
    assert outcome.accepted_revisions == 1
    assert len(repo.revisions) == 3
    assert repo.list_latest(USER_A, 50)[0].title == "Push Day — corrected"
    old = repo.get_by_revision_id(USER_A, UUID(int=1))
    assert old is not None and old.session.title == "Push Day"


def test_same_revision_with_conflicting_payload_fails_without_overwrite() -> None:
    repo = InMemoryDetailedTrainingRepository(ids=lambda: UUID(int=1))
    batch = _initial_batch()
    repo.apply_import(USER_A, batch, NOW)
    changed = replace(
        batch.sessions[0],
        title="Conflicting title",
        source_payload_sha256="f" * 64,
    )
    with pytest.raises(DetailedTrainingRevisionConflictError):
        repo.apply_import(
            USER_A,
            DetailedTrainingImportBatch((changed,), (), True),
            NOW,
        )
    assert repo.get_by_revision_id(USER_A, UUID(int=1)).session.title == "Push Day"  # type: ignore[union-attr]


def test_conflict_rolls_back_earlier_new_revision_in_same_import() -> None:
    ids = iter((UUID(int=1), UUID(int=2), UUID(int=3)))
    repo = InMemoryDetailedTrainingRepository(ids=lambda: next(ids))
    batch = _initial_batch()
    repo.apply_import(USER_A, DetailedTrainingImportBatch((batch.sessions[0],), (), True), NOW)
    new_session = batch.sessions[1]
    conflict = replace(batch.sessions[0], title="conflict", source_payload_sha256="f" * 64)
    with pytest.raises(DetailedTrainingRevisionConflictError):
        repo.apply_import(
            USER_A, DetailedTrainingImportBatch((new_session, conflict), (), True), NOW
        )
    assert len(repo.revisions) == 1


def test_deletion_hides_all_revisions_and_prevents_resurrection() -> None:
    repo = InMemoryDetailedTrainingRepository(ids=lambda: UUID(int=1))
    batch = _initial_batch()
    push = batch.sessions[0]
    repo.apply_import(USER_A, DetailedTrainingImportBatch((push,), (), True), NOW)
    deletion = replace(batch.deletions[0], source_session_id=push.source_session_id)
    removed = repo.apply_import(USER_A, DetailedTrainingImportBatch((), (deletion,), True), NOW)
    blocked = repo.apply_import(USER_A, DetailedTrainingImportBatch((push,), (), True), NOW)
    assert removed.applied_deletions == 1
    assert blocked.blocked_by_tombstone == 1
    assert repo.list_latest(USER_A, 50) == ()
    assert repo.get_by_revision_id(USER_A, UUID(int=1)) is None


def test_owner_scope_and_detail_read_return_immutable_values() -> None:
    repo = InMemoryDetailedTrainingRepository(ids=lambda: UUID(int=1))
    repo.apply_import(USER_A, _initial_batch(), NOW)
    assert len(repo.list_latest(USER_A, 50)) == 2
    assert repo.list_latest(USER_B, 50) == ()
    assert (
        GetDetailedTrainingSessionUseCase(repo).execute(user_id=USER_B, revision_id=UUID(int=1))
        is None
    )


def test_incomplete_source_batch_is_rejected_before_repository_mutation() -> None:
    class _IncompleteSource:
        def load_changes(self, since: datetime | None) -> DetailedTrainingImportBatch:
            del since
            return DetailedTrainingImportBatch((), (), False)

    repo = InMemoryDetailedTrainingRepository()
    use_case = ImportDetailedTrainingSessionsUseCase(_IncompleteSource(), repo, _Clock())
    with pytest.raises(IncompleteDetailedTrainingImportError):
        use_case.execute(user_id=USER_A)
    assert repo.revisions == {} and repo.tombstones == {}


def test_list_limit_is_bounded() -> None:
    use_case = ListDetailedTrainingSessionsUseCase(InMemoryDetailedTrainingRepository())
    with pytest.raises(ValueError):
        use_case.execute(user_id=USER_A, limit=0)
    with pytest.raises(ValueError):
        use_case.execute(user_id=USER_A, limit=101)
