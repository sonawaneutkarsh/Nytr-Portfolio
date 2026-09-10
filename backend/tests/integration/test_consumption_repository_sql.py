"""SqlConsumptionRepository behavior and in-memory parity on real PostgreSQL."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.application.ports import DuplicateConsumptionError
from nutrition_agent.db.in_memory_repos import InMemoryConsumptionRepository
from nutrition_agent.domain.consumption import ConsumptionEntry, ConsumptionState
from nutrition_agent.domain.planning.artifacts import (
    PlanItem,
    PlanRun,
    PlanRunStatus,
    PlanVersion,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; repository test requires scratch Postgres",
)


@dataclass(frozen=True)
class _Graph:
    user_id: UUID
    run_id: UUID
    version_id: UUID
    item_ids: tuple[UUID, UUID]


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _persist_graph(user_id: UUID) -> _Graph:
    from nutrition_agent.db.sql_repos import SqlPlanRunRepository

    run = PlanRun(
        run_id=uuid4(),
        user_id=user_id,
        requested_for_date=date(2034, 5, 1),
        timezone="America/New_York",
        inputs_fingerprint=f"consumption-repo-{uuid4().hex}",
        status=PlanRunStatus.COMPLETED,
        reason_codes=(),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    version = PlanVersion(
        version_id=uuid4(),
        run_id=run.run_id,
        plan_jsonb={
            "artifact_kind": "daily_plan",
            "slots": [
                {
                    "candidates": [
                        {
                            "candidate_id": f"candidate-{index}",
                            "lines": [{"name_normalized": f"Meal {index}"}],
                            "totals": {
                                "confidence": "official_published",
                                "declared_unavailable": [],
                                "published_zero": [],
                                "presences": {
                                    "calories_kcal": "known_value",
                                    "protein_g": "known_value",
                                },
                                "quantities": {
                                    "calories_kcal": "400",
                                    "protein_g": "30",
                                },
                            },
                        }
                    ]
                }
                for index in range(2)
            ],
        },
        plan_canonical='{"artifact_kind":"daily_plan","slots":[]}',
        plan_sha256=uuid4().hex,
    )
    items = tuple(
        PlanItem(
            item_id=uuid4(),
            version_id=version.version_id,
            slot_index=index,
            context="post_workout_lunch",
            rank=1,
            candidate_id=f"candidate-{index}",
            menu_period="Lunch",
            food_ids=(),
            offering_ids=(),
            profile_row_ids=(),
            profile_content_sha256s=(),
            score_total="1",
            calories_kcal="400",
        )
        for index in range(2)
    )
    SqlPlanRunRepository(DATABASE_URL).save(run, version, items)
    return _Graph(user_id, run.run_id, version.version_id, (items[0].item_id, items[1].item_id))


def _entry(
    graph: _Graph,
    *,
    entry_id: UUID,
    event_id: UUID,
    recorded_at: datetime,
    item_id: UUID | None = None,
    run_id: UUID | None = None,
    state: ConsumptionState = ConsumptionState.EATEN,
    user_id: UUID | None = None,
) -> ConsumptionEntry:
    return ConsumptionEntry(
        entry_id=entry_id,
        user_id=user_id or graph.user_id,
        plan_run_id=run_id or graph.run_id,
        plan_version_id=graph.version_id,
        item_id=item_id or graph.item_ids[0],
        state=state,
        client_event_id=event_id,
        recorded_at=recorded_at,
    )


def test_sql_save_replay_conflicts_and_primary_key_collision() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlConsumptionRepository

    graph = _persist_graph(uuid4())
    repository = SqlConsumptionRepository(DATABASE_URL)
    first_time = datetime(2034, 5, 1, 12, 0, tzinfo=UTC)
    original = _entry(graph, entry_id=uuid4(), event_id=uuid4(), recorded_at=first_time)

    first = repository.save(original)
    assert first.created is True
    assert first.entry == original

    replay_input = replace(
        original,
        entry_id=uuid4(),
        recorded_at=first_time + timedelta(minutes=5),
    )
    replay = repository.save(replay_input)
    assert replay.created is False
    assert replay.entry == original
    assert replay.entry.entry_id != replay_input.entry_id
    assert replay.entry.recorded_at != replay_input.recorded_at

    for conflict in (
        replace(replay_input, state=ConsumptionState.SKIPPED),
        replace(replay_input, plan_run_id=uuid4()),
    ):
        with pytest.raises(DuplicateConsumptionError):
            repository.save(conflict)
        assert repository.list_for_run(graph.user_id, graph.run_id) == (original,)

    with pytest.raises(DuplicateConsumptionError, match="entry_id"):
        repository.save(replace(original, client_event_id=uuid4()))
    assert repository.list_for_run(graph.user_id, graph.run_id) == (original,)


def test_sql_distinct_events_and_list_order_are_owner_run_scoped() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlConsumptionRepository

    graph = _persist_graph(uuid4())
    other_graph = _persist_graph(graph.user_id)
    repository = SqlConsumptionRepository(DATABASE_URL)
    base = datetime(2034, 5, 1, 12, 0, tzinfo=UTC)
    tied_earlier_id, tied_later_id = sorted((uuid4(), uuid4()))
    later = _entry(
        graph,
        entry_id=uuid4(),
        event_id=uuid4(),
        recorded_at=base + timedelta(minutes=1),
    )
    tied_second = _entry(
        graph,
        entry_id=tied_later_id,
        event_id=uuid4(),
        recorded_at=base,
        item_id=graph.item_ids[1],
    )
    tied_first = _entry(
        graph,
        entry_id=tied_earlier_id,
        event_id=uuid4(),
        recorded_at=base,
    )
    other_run = _entry(
        other_graph,
        entry_id=uuid4(),
        event_id=uuid4(),
        recorded_at=base,
    )
    for event in (later, tied_second, other_run, tied_first):
        assert repository.save(event).created is True

    result = repository.list_for_run(graph.user_id, graph.run_id)
    assert isinstance(result, tuple)
    assert result == (tied_first, tied_second, later)
    assert repository.list_for_run(graph.user_id, other_graph.run_id) == (other_run,)
    assert repository.list_for_run(uuid4(), graph.run_id) == ()


def test_sql_cross_user_parent_attack_is_blocked_and_foreign_rows_are_invisible() -> None:
    _apply_migrations()
    from psycopg import errors as psycopg_errors

    from nutrition_agent.db.sql_repos import SqlConsumptionRepository

    owner_graph = _persist_graph(uuid4())
    repository = SqlConsumptionRepository(DATABASE_URL)
    original = _entry(
        owner_graph,
        entry_id=uuid4(),
        event_id=uuid4(),
        recorded_at=datetime(2034, 5, 1, 12, 0, tzinfo=UTC),
    )
    repository.save(original)

    attacker = uuid4()
    malicious = replace(
        original,
        entry_id=uuid4(),
        user_id=attacker,
        client_event_id=uuid4(),
    )
    with pytest.raises(psycopg_errors.InsufficientPrivilege):
        repository.save(malicious)
    assert repository.list_for_run(attacker, owner_graph.run_id) == ()
    assert repository.list_for_run(owner_graph.user_id, owner_graph.run_id) == (original,)


def test_sql_and_in_memory_consumption_contract_parity() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlConsumptionRepository

    graph = _persist_graph(uuid4())
    repositories = (
        InMemoryConsumptionRepository(),
        SqlConsumptionRepository(DATABASE_URL),
    )
    base = datetime(2034, 5, 1, 12, 0, tzinfo=UTC)
    original = _entry(graph, entry_id=uuid4(), event_id=uuid4(), recorded_at=base)
    replay = replace(original, entry_id=uuid4(), recorded_at=base + timedelta(minutes=1))
    later = _entry(
        graph,
        entry_id=uuid4(),
        event_id=uuid4(),
        recorded_at=base + timedelta(minutes=2),
    )

    outcomes: list[tuple[bool, bool, UUID, datetime, tuple[ConsumptionEntry, ...]]] = []
    for repository in repositories:
        created = repository.save(original)
        replayed = repository.save(replay)
        with pytest.raises(DuplicateConsumptionError):
            repository.save(replace(replay, state=ConsumptionState.SKIPPED))
        with pytest.raises(DuplicateConsumptionError):
            repository.save(replace(replay, plan_run_id=uuid4()))
        repository.save(later)
        outcomes.append(
            (
                created.created,
                replayed.created,
                replayed.entry.entry_id,
                replayed.entry.recorded_at,
                repository.list_for_run(graph.user_id, graph.run_id),
            )
        )

    assert outcomes[0] == outcomes[1]


def test_sql_daily_ledger_read_is_owner_date_scoped_and_survives_regeneration() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlConsumptionRepository

    owner = uuid4()
    old_plan = _persist_graph(owner)
    regenerated_plan = _persist_graph(owner)
    repository = SqlConsumptionRepository(DATABASE_URL)
    inside_first = datetime(2034, 5, 1, 15, 0, tzinfo=UTC)
    inside_second = datetime(2034, 5, 1, 22, 0, tzinfo=UTC)
    outside = datetime(2034, 5, 2, 4, 0, tzinfo=UTC)
    events = (
        _entry(
            old_plan,
            entry_id=uuid4(),
            event_id=uuid4(),
            recorded_at=inside_first,
        ),
        _entry(
            regenerated_plan,
            entry_id=uuid4(),
            event_id=uuid4(),
            recorded_at=inside_second,
        ),
        _entry(
            regenerated_plan,
            entry_id=uuid4(),
            event_id=uuid4(),
            recorded_at=inside_second,
            item_id=regenerated_plan.item_ids[1],
            state=ConsumptionState.SKIPPED,
        ),
        _entry(
            old_plan,
            entry_id=uuid4(),
            event_id=uuid4(),
            recorded_at=outside,
            item_id=old_plan.item_ids[1],
        ),
    )
    for event in events:
        repository.save(event)

    evidence = repository.list_eaten_evidence(
        owner,
        datetime(2034, 5, 1, 4, tzinfo=UTC),
        datetime(2034, 5, 2, 4, tzinfo=UTC),
    )

    assert tuple(item.entry_id for item in evidence) == (events[0].entry_id, events[1].entry_id)
    assert tuple(item.plan_run_id for item in evidence) == (
        old_plan.run_id,
        regenerated_plan.run_id,
    )
    assert all(item.calories_kcal == Decimal("400") for item in evidence)
    assert all(item.protein_g == Decimal("30") for item in evidence)
    assert (
        repository.list_eaten_evidence(
            uuid4(),
            datetime(2034, 5, 1, 4, tzinfo=UTC),
            datetime(2034, 5, 2, 4, tzinfo=UTC),
        )
        == ()
    )
