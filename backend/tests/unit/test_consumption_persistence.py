"""M7 Step 3 consumption domain and in-memory persistence semantics."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from nutrition_agent.application.ports import DuplicateConsumptionError
from nutrition_agent.db.in_memory_repos import InMemoryConsumptionRepository
from nutrition_agent.domain.consumption import ConsumptionEntry, ConsumptionState

USER_A = UUID(int=1)
USER_B = UUID(int=2)
RUN_A = UUID(int=10)
RUN_B = UUID(int=11)
VERSION_A = UUID(int=20)
ITEM_A = UUID(int=30)
EVENT_A = UUID(int=40)
ENTRY_A = UUID(int=50)
RECORDED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _entry(
    *,
    entry_id: UUID = ENTRY_A,
    user_id: UUID = USER_A,
    plan_run_id: UUID = RUN_A,
    plan_version_id: UUID = VERSION_A,
    item_id: UUID = ITEM_A,
    state: ConsumptionState = ConsumptionState.EATEN,
    client_event_id: UUID = EVENT_A,
    recorded_at: datetime = RECORDED_AT,
) -> ConsumptionEntry:
    return ConsumptionEntry(
        entry_id=entry_id,
        user_id=user_id,
        plan_run_id=plan_run_id,
        plan_version_id=plan_version_id,
        item_id=item_id,
        state=state,
        client_event_id=client_event_id,
        recorded_at=recorded_at,
    )


def test_consumption_state_has_exactly_the_authorized_values() -> None:
    assert [(state.name, state.value) for state in ConsumptionState] == [
        ("EATEN", "eaten"),
        ("SKIPPED", "skipped"),
        ("UNAVAILABLE", "unavailable"),
        ("ALTERNATIVE", "alternative"),
    ]
    with pytest.raises(ValueError):
        ConsumptionState("consumed")


def test_consumption_entry_requires_timezone_aware_recorded_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _entry(recorded_at=datetime(2026, 8, 21, 12, 0))


def test_first_save_returns_supplied_stored_entry_as_created() -> None:
    repository = InMemoryConsumptionRepository()
    entry = _entry()

    outcome = repository.save(entry)

    assert outcome.created is True
    assert outcome.entry is entry
    assert repository.list_for_run(USER_A, RUN_A) == (entry,)


def test_exact_replay_returns_original_without_duplicate() -> None:
    repository = InMemoryConsumptionRepository()
    original = _entry()
    repository.save(original)

    outcome = repository.save(_entry())

    assert outcome.created is False
    assert outcome.entry is original
    assert outcome.entry.entry_id == original.entry_id
    assert outcome.entry.recorded_at == original.recorded_at
    assert outcome.entry.state is ConsumptionState.EATEN
    assert repository.list_for_run(USER_A, RUN_A) == (original,)


def test_replay_with_new_entry_id_returns_original_entry_id() -> None:
    repository = InMemoryConsumptionRepository()
    original = _entry()
    repository.save(original)

    outcome = repository.save(_entry(entry_id=UUID(int=51)))

    assert outcome.created is False
    assert outcome.entry is original
    assert outcome.entry.entry_id == UUID(int=50)
    assert len(repository.list_for_run(USER_A, RUN_A)) == 1


def test_replay_with_new_timestamp_preserves_original_timestamp() -> None:
    repository = InMemoryConsumptionRepository()
    original = _entry()
    repository.save(original)

    outcome = repository.save(_entry(recorded_at=RECORDED_AT + timedelta(minutes=5)))

    assert outcome.created is False
    assert outcome.entry is original
    assert outcome.entry.recorded_at == RECORDED_AT
    assert len(repository.list_for_run(USER_A, RUN_A)) == 1


@pytest.mark.parametrize(
    ("conflicting",),
    [
        (_entry(state=ConsumptionState.SKIPPED),),
        (_entry(plan_run_id=RUN_B),),
    ],
    ids=["state", "plan-run"],
)
def test_idempotency_key_conflict_raises_without_overwriting(
    conflicting: ConsumptionEntry,
) -> None:
    repository = InMemoryConsumptionRepository()
    original = _entry()
    repository.save(original)

    with pytest.raises(DuplicateConsumptionError):
        repository.save(conflicting)

    assert repository.list_for_run(USER_A, RUN_A) == (original,)
    assert repository.list_for_run(USER_A, RUN_B) == ()


def test_different_client_event_id_creates_separate_event() -> None:
    repository = InMemoryConsumptionRepository()
    first = _entry()
    second = _entry(entry_id=UUID(int=51), client_event_id=UUID(int=41))

    assert repository.save(first).created is True
    assert repository.save(second).created is True
    assert repository.list_for_run(USER_A, RUN_A) == (first, second)


def test_different_item_id_creates_separate_event() -> None:
    repository = InMemoryConsumptionRepository()
    first = _entry()
    second = _entry(entry_id=UUID(int=51), item_id=UUID(int=31))

    assert repository.save(first).created is True
    assert repository.save(second).created is True
    assert repository.list_for_run(USER_A, RUN_A) == (first, second)


def test_duplicate_entry_id_for_different_event_is_rejected() -> None:
    repository = InMemoryConsumptionRepository()
    original = _entry()
    repository.save(original)

    with pytest.raises(DuplicateConsumptionError, match="entry_id"):
        repository.save(_entry(client_event_id=UUID(int=41)))

    assert repository.list_for_run(USER_A, RUN_A) == (original,)


def test_list_for_run_filters_owner_and_run_with_deterministic_order() -> None:
    repository = InMemoryConsumptionRepository()
    later = _entry(
        entry_id=UUID(int=53),
        client_event_id=UUID(int=43),
        recorded_at=RECORDED_AT + timedelta(minutes=1),
    )
    tied_second = _entry(entry_id=UUID(int=52), client_event_id=UUID(int=42))
    tied_first = _entry(entry_id=UUID(int=51), client_event_id=UUID(int=41))
    other_run = _entry(
        entry_id=UUID(int=54),
        plan_run_id=RUN_B,
        client_event_id=UUID(int=44),
    )
    other_user = _entry(
        entry_id=UUID(int=55),
        user_id=USER_B,
        client_event_id=UUID(int=45),
    )
    for entry in (later, tied_second, other_run, other_user, tied_first):
        repository.save(entry)

    assert repository.list_for_run(USER_A, RUN_A) == (tied_first, tied_second, later)
    assert repository.list_for_run(USER_A, RUN_B) == (other_run,)
    assert repository.list_for_run(USER_B, RUN_A) == (other_user,)


def test_repository_and_entries_expose_no_mutation_api() -> None:
    repository = InMemoryConsumptionRepository()
    entry = _entry()
    repository.save(entry)

    for forbidden in ("update", "revoke", "undo", "delete", "set_state", "replace"):
        assert not hasattr(repository, forbidden)
    assert not hasattr(repository, "entries")
    assert not hasattr(repository, "rows")
    assert isinstance(repository.list_for_run(USER_A, RUN_A), tuple)
    with pytest.raises(FrozenInstanceError):
        entry.state = ConsumptionState.SKIPPED  # type: ignore[misc]
