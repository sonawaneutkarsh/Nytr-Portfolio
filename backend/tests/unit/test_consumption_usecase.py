"""M7 Step 4 application use cases for immutable consumption events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from nutrition_agent.application.consumption import (
    ListConsumptionForRunUseCase,
    RecordConsumptionUseCase,
)
from nutrition_agent.application.ports import DuplicateConsumptionError
from nutrition_agent.db.in_memory_repos import InMemoryConsumptionRepository
from nutrition_agent.domain.consumption import ConsumptionState

USER_A = UUID(int=1)
USER_B = UUID(int=2)
RUN_A = UUID(int=10)
RUN_B = UUID(int=11)
VERSION_A = UUID(int=20)
ITEM_A = UUID(int=30)
ITEM_B = UUID(int=31)
EVENT_A = UUID(int=40)
EVENT_B = UUID(int=41)
ENTRY_A = UUID(int=50)
ENTRY_B = UUID(int=51)
TIME_A = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
TIME_B = TIME_A + timedelta(minutes=5)


class _Clock:
    def __init__(self, *values: datetime) -> None:
        self._values = values
        self.calls = 0

    def now(self) -> datetime:
        value = self._values[self.calls]
        self.calls += 1
        return value


class _Ids:
    def __init__(self, *values: UUID) -> None:
        self._values = values
        self.calls = 0

    def new_id(self) -> UUID:
        value = self._values[self.calls]
        self.calls += 1
        return value


def _execute(
    use_case: RecordConsumptionUseCase,
    *,
    user_id: UUID = USER_A,
    plan_run_id: UUID = RUN_A,
    plan_version_id: UUID = VERSION_A,
    item_id: UUID = ITEM_A,
    state: ConsumptionState = ConsumptionState.EATEN,
    client_event_id: UUID = EVENT_A,
):
    return use_case.execute(
        user_id=user_id,
        plan_run_id=plan_run_id,
        plan_version_id=plan_version_id,
        item_id=item_id,
        state=state,
        client_event_id=client_event_id,
    )


def test_first_record_generates_metadata_and_forwards_every_field() -> None:
    repository = InMemoryConsumptionRepository()
    clock = _Clock(TIME_A)
    ids = _Ids(ENTRY_A)
    use_case = RecordConsumptionUseCase(repository=repository, clock=clock, ids=ids)

    outcome = _execute(use_case, state=ConsumptionState.UNAVAILABLE)

    assert outcome.created is True
    assert outcome.entry.entry_id == ENTRY_A
    assert outcome.entry.recorded_at == TIME_A
    assert outcome.entry.recorded_at.utcoffset() is not None
    assert outcome.entry.user_id == USER_A
    assert outcome.entry.plan_run_id == RUN_A
    assert outcome.entry.plan_version_id == VERSION_A
    assert outcome.entry.item_id == ITEM_A
    assert outcome.entry.state is ConsumptionState.UNAVAILABLE
    assert outcome.entry.client_event_id == EVENT_A
    assert clock.calls == ids.calls == 1


def test_retry_generates_new_metadata_but_returns_original_persisted_metadata() -> None:
    repository = InMemoryConsumptionRepository()
    clock = _Clock(TIME_A, TIME_B)
    ids = _Ids(ENTRY_A, ENTRY_B)
    use_case = RecordConsumptionUseCase(repository=repository, clock=clock, ids=ids)

    first = _execute(use_case)
    replay = _execute(use_case)

    assert first.created is True
    assert replay.created is False
    assert clock.calls == ids.calls == 2
    assert replay.entry is first.entry
    assert replay.entry.entry_id == ENTRY_A
    assert replay.entry.entry_id != ENTRY_B
    assert replay.entry.recorded_at == TIME_A
    assert replay.entry.recorded_at != TIME_B
    assert repository.list_for_run(USER_A, RUN_A) == (first.entry,)


def test_conflicting_state_propagates_duplicate_error_without_mutation() -> None:
    repository = InMemoryConsumptionRepository()
    use_case = RecordConsumptionUseCase(
        repository=repository,
        clock=_Clock(TIME_A, TIME_B),
        ids=_Ids(ENTRY_A, ENTRY_B),
    )
    original = _execute(use_case)

    with pytest.raises(DuplicateConsumptionError):
        _execute(use_case, state=ConsumptionState.SKIPPED)

    assert repository.list_for_run(USER_A, RUN_A) == (original.entry,)
    assert original.entry.state is ConsumptionState.EATEN


def test_conflicting_plan_run_propagates_duplicate_error_without_mutation() -> None:
    repository = InMemoryConsumptionRepository()
    use_case = RecordConsumptionUseCase(
        repository=repository,
        clock=_Clock(TIME_A, TIME_B),
        ids=_Ids(ENTRY_A, ENTRY_B),
    )
    original = _execute(use_case)

    with pytest.raises(DuplicateConsumptionError):
        _execute(use_case, plan_run_id=RUN_B)

    assert repository.list_for_run(USER_A, RUN_A) == (original.entry,)
    assert repository.list_for_run(USER_A, RUN_B) == ()


def test_different_client_event_creates_distinct_immutable_event() -> None:
    repository = InMemoryConsumptionRepository()
    use_case = RecordConsumptionUseCase(
        repository=repository,
        clock=_Clock(TIME_A, TIME_B),
        ids=_Ids(ENTRY_A, ENTRY_B),
    )

    first = _execute(use_case)
    second = _execute(use_case, client_event_id=EVENT_B)

    assert first.created is True and second.created is True
    assert repository.list_for_run(USER_A, RUN_A) == (first.entry, second.entry)


def test_different_item_creates_distinct_immutable_event() -> None:
    repository = InMemoryConsumptionRepository()
    use_case = RecordConsumptionUseCase(
        repository=repository,
        clock=_Clock(TIME_A, TIME_B),
        ids=_Ids(ENTRY_A, ENTRY_B),
    )

    first = _execute(use_case)
    second = _execute(use_case, item_id=ITEM_B)

    assert first.created is True and second.created is True
    assert repository.list_for_run(USER_A, RUN_A) == (first.entry, second.entry)


def test_list_use_case_preserves_owner_run_filtering_and_repository_order() -> None:
    repository = InMemoryConsumptionRepository()
    record = RecordConsumptionUseCase(
        repository=repository,
        clock=_Clock(TIME_B, TIME_A, TIME_A, TIME_A),
        ids=_Ids(UUID(int=53), UUID(int=52), UUID(int=54), UUID(int=55)),
    )
    later = _execute(record, client_event_id=UUID(int=43))
    earlier = _execute(record, client_event_id=UUID(int=42))
    _execute(record, plan_run_id=RUN_B, client_event_id=UUID(int=44))
    _execute(record, user_id=USER_B, client_event_id=UUID(int=45))

    outcome = ListConsumptionForRunUseCase(repository).execute(
        user_id=USER_A,
        plan_run_id=RUN_A,
    )

    assert outcome == (earlier.entry, later.entry)
    assert isinstance(outcome, tuple)


def test_naive_clock_fails_closed_without_persisting() -> None:
    repository = InMemoryConsumptionRepository()
    use_case = RecordConsumptionUseCase(
        repository=repository,
        clock=_Clock(datetime(2026, 8, 21, 12, 0)),
        ids=_Ids(ENTRY_A),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        _execute(use_case)

    assert repository.list_for_run(USER_A, RUN_A) == ()


def test_invalid_runtime_state_fails_before_generating_or_persisting() -> None:
    repository = InMemoryConsumptionRepository()
    clock = _Clock(TIME_A)
    ids = _Ids(ENTRY_A)
    use_case = RecordConsumptionUseCase(repository=repository, clock=clock, ids=ids)

    with pytest.raises(ValueError, match="ConsumptionState"):
        _execute(use_case, state=cast(ConsumptionState, "eaten"))

    assert clock.calls == ids.calls == 0
    assert repository.list_for_run(USER_A, RUN_A) == ()


@pytest.mark.parametrize(
    ("user_id", "plan_run_id", "plan_version_id", "item_id", "client_event_id"),
    [
        (cast(UUID, "bad"), RUN_A, VERSION_A, ITEM_A, EVENT_A),
        (USER_A, cast(UUID, "bad"), VERSION_A, ITEM_A, EVENT_A),
        (USER_A, RUN_A, cast(UUID, "bad"), ITEM_A, EVENT_A),
        (USER_A, RUN_A, VERSION_A, cast(UUID, "bad"), EVENT_A),
        (USER_A, RUN_A, VERSION_A, ITEM_A, cast(UUID, "bad")),
    ],
)
def test_required_identifiers_fail_closed_before_persistence(
    user_id: UUID,
    plan_run_id: UUID,
    plan_version_id: UUID,
    item_id: UUID,
    client_event_id: UUID,
) -> None:
    repository = InMemoryConsumptionRepository()
    clock = _Clock(TIME_A)
    ids = _Ids(ENTRY_A)
    use_case = RecordConsumptionUseCase(repository=repository, clock=clock, ids=ids)

    with pytest.raises(ValueError, match="UUID"):
        _execute(
            use_case,
            user_id=user_id,
            plan_run_id=plan_run_id,
            plan_version_id=plan_version_id,
            item_id=item_id,
            client_event_id=client_event_id,
        )

    assert clock.calls == ids.calls == 0
    assert repository.list_for_run(USER_A, RUN_A) == ()


def test_use_cases_expose_no_mutation_operations() -> None:
    repository = InMemoryConsumptionRepository()
    record = RecordConsumptionUseCase(repository, _Clock(TIME_A), _Ids(ENTRY_A))
    listing = ListConsumptionForRunUseCase(repository)

    for use_case in (record, listing):
        for forbidden in ("update", "revoke", "undo", "delete", "set_state", "replace"):
            assert not hasattr(use_case, forbidden)
