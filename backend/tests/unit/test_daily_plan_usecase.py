"""M6 use-case tests: generate-or-replay, NO_PLAN fidelity, provenance pins."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.daily_plan import (
    DailyPlanInputs,
    GenerateDailyPlanUseCase,
    compute_inputs_fingerprint,
)
from nutrition_agent.db.in_memory_repos import InMemoryPlanRunRepository
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, PeriodMenu
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey
from tests.unit.planning_helpers import make_offering

USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
PLAN_DATE = date(2026, 8, 21)
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
MENU_SHA = "usecase-menu-sha"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    _n = 0

    def new_id(self) -> UUID:
        _Ids._n += 1
        return UUID(int=_Ids._n)


def _schedule() -> WeeklySchedule:
    lunch_block = ScheduleBlock(
        kind=BlockKind.STACKS_MEAL,
        start=time(12, 30),
        end=time(13, 15),
        label="stacks lunch",
        meal_context=MealContext.POST_WORKOUT_LUNCH,
    )
    return WeeklySchedule(
        version="usecase-schedule.v1",
        timezone="America/New_York",
        days={
            **{w: DaySchedule(weekday=w, blocks=()) for w in Weekday},
            Weekday.FRIDAY: DaySchedule(weekday=Weekday.FRIDAY, blocks=(lunch_block,)),
        },
    )


def _policy() -> PlannerPolicy:
    return PlannerPolicy(
        version="usecase-planner.v1",
        context_period={MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH},
        slot_shares={},
    )


def _slot_policies() -> dict[MealContext, SlotPolicy]:
    return {MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)}


def _targets() -> TargetSet:
    return TargetSet(
        policy_version="usecase-targets.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal("900"), weight=Decimal("1")
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal("50"), weight=Decimal("1")
            ),
        },
    )


def _menu(empty_lunch: bool = False):
    if empty_lunch:
        lunch = PeriodMenu(offerings=(), explicitly_empty=True)
        offerings = []
    else:
        o1 = make_offering(1).build()
        o2 = make_offering(2).build()
        lunch = PeriodMenu(offerings=(o1, o2))
        offerings = [o1, o2]
    view = MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: lunch},
        fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
        snapshot_sha256=MENU_SHA,
    )
    # offering_id == food_number * 100 per helper; profile rows deterministic ids.
    profile_pins = {str(o.offering_id): UUID(int=o.food_id.int % 1000 + 5000) for o in offerings}
    return view, profile_pins


def _inputs(empty_lunch: bool = False, user_id: UUID = USER_A) -> DailyPlanInputs:
    view, pins = _menu(empty_lunch)
    return DailyPlanInputs(
        user_id=user_id,
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        schedule=_schedule(),
        exceptions=(),
        menu=view,
        policy=_policy(),
        slot_policies=dict(_slot_policies()),
        targets=_targets(),
        target_policy_version_id=UUID(int=777),
        offering_profile_ids=pins,
    )


def test_completed_plan_persists_items_with_provenance_pins() -> None:
    repo = InMemoryPlanRunRepository()
    use_case = GenerateDailyPlanUseCase(runs=repo, clock=_Clock(), ids=_Ids())

    outcome = use_case.execute(_inputs(), plan_at=NOW)
    assert outcome.replayed is False
    assert outcome.run is not None and outcome.run.status.value == "completed"
    assert outcome.run.target_policy_version_id == UUID(int=777)
    assert outcome.version is not None and len(outcome.version.plan_sha256) == 64

    items = outcome.items
    assert len(items) >= 1
    first = items[0]
    inputs = _inputs()
    expected_offering = str(inputs.menu.periods[MealPeriod.LUNCH].offerings[0].offering_id)
    assert expected_offering in {str(x) for x in first.offering_ids}
    expected_pins = [inputs.offering_profile_ids[str(oid)] for oid in first.offering_ids]
    assert list(first.profile_row_ids) == expected_pins
    assert (
        len(first.profile_content_sha256s) == len(first.profile_row_ids) == len(first.offering_ids)
    )


def test_identical_inputs_replay_without_duplicate_run() -> None:
    repo = InMemoryPlanRunRepository()
    use_case = GenerateDailyPlanUseCase(runs=repo, clock=_Clock(), ids=_Ids())
    inputs = _inputs()

    first = use_case.execute(inputs, plan_at=NOW)
    second = use_case.execute(inputs, plan_at=NOW.replace(minute=5))

    assert first.replayed is False and second.replayed is True
    assert second.version is None  # no new artifact written
    view = second.replay_view
    assert view is not None
    assert view.plan_sha256 == first.version.plan_sha256
    assert view.target_policy_version_id == UUID(int=777)
    total_runs = len(repo.runs)
    assert total_runs == 1


def test_different_user_same_menu_is_separate_run_and_isolated() -> None:
    from uuid import UUID as _UUID

    other = _UUID("00000000-0000-0000-0000-0000000000b2")
    repo = InMemoryPlanRunRepository()
    use_case = GenerateDailyPlanUseCase(runs=repo, clock=_Clock(), ids=_Ids())
    a = use_case.execute(_inputs(), plan_at=NOW)
    b = use_case.execute(_inputs(user_id=other), plan_at=NOW)
    assert a.replayed is False and b.replayed is False
    assert a.run is not None and b.run is not None
    assert a.run.user_id != b.run.user_id


@pytest.mark.parametrize(
    ("empty_lunch", "expected_reason"),
    [(True, "empty_menu_period"), (False, "menu_data_unavailable")],
)
def test_no_plan_states_first_class_with_fidelity(empty_lunch: bool, expected_reason: str) -> None:
    empty_lunch_flag = empty_lunch
    if not empty_lunch_flag:
        # Menu without ANY periods => data gap rather than source emptiness.
        inputs = _inputs()
        from nutrition_agent.domain.planning.menu_view import MenuDayView as _V

        gap = _V(
            service_date=PLAN_DATE,
            periods={},
            fetched_at=inputs.menu.fetched_at,
            snapshot_sha256=inputs.menu.snapshot_sha256,
        )
        inputs = DailyPlanInputs(**{**inputs.__dict__, "menu": gap})
    else:
        inputs = _inputs(empty_lunch=True)

    repo = InMemoryPlanRunRepository()
    use_case = GenerateDailyPlanUseCase(runs=repo, clock=_Clock(), ids=_Ids())
    outcome = use_case.execute(inputs, plan_at=NOW)

    assert outcome.run is not None and outcome.run.status.value == "no_plan"
    assert outcome.run.reason_codes == (expected_reason,)
    assert outcome.items == ()
    stored = repo.latest_for_user_date(USER_A, PLAN_DATE)
    assert stored is not None
    assert stored.status == "no_plan"
    assert stored.reason_codes == (expected_reason,)
    assert stored.plan_canonical is None or '"status":"no_plan"' in stored.plan_canonical


def test_zero_slot_reason_is_persisted_without_fabricating_a_slot() -> None:
    inputs = _inputs()
    empty_schedule = WeeklySchedule(
        version="empty-schedule.v1",
        timezone=inputs.schedule.timezone,
        days={weekday: DaySchedule(weekday=weekday, blocks=()) for weekday in Weekday},
    )
    repo = InMemoryPlanRunRepository()
    use_case = GenerateDailyPlanUseCase(runs=repo, clock=_Clock(), ids=_Ids())

    outcome = use_case.execute(replace(inputs, schedule=empty_schedule), plan_at=NOW)

    assert outcome.run is not None
    assert outcome.run.status.value == "no_plan"
    assert outcome.run.reason_codes == ("no_resolved_meal_slots",)
    assert outcome.version is not None
    assert outcome.version.plan_jsonb["slots"] == []
    assert outcome.version.plan_jsonb["failure_reasons"] == ["no_resolved_meal_slots"]
    stored = repo.latest_for_user_date(USER_A, PLAN_DATE)
    assert stored is not None
    assert stored.reason_codes == ("no_resolved_meal_slots",)


def test_fingerprint_excludes_clock_binds_snapshot_and_target_version() -> None:
    base = compute_inputs_fingerprint(_inputs())
    # Identical everything => identical fingerprint.
    assert compute_inputs_fingerprint(_inputs()) == base
    # Different menu snapshot => different fingerprint.
    view, pins = _menu()
    shifted = DailyPlanInputs(
        **{
            **_inputs().__dict__,
            "menu": MenuDayView(
                service_date=PLAN_DATE,
                periods=view.periods,
                fetched_at=view.fetched_at,
                snapshot_sha256="other-sha",
            ),
        }
    )
    assert compute_inputs_fingerprint(shifted) != base


def test_offline_fixture_pipeline_end_to_end(tmp_path=None):  # type: ignore[no-untyped-def]
    """Full fixture chain through the seeded ingestion repos (offline only)."""
    from pathlib import Path

    from nutrition_agent.scripts.generate_daily_plan import build_demo_inputs

    root = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"
    inputs, use_case = build_demo_inputs(root)
    outcome = use_case.execute(inputs, plan_at=NOW)
    assert outcome.run is not None
    assert outcome.run.status.value == "completed", outcome.run.reason_codes
    assert outcome.version is not None
    assert compute_inputs_fingerprint(inputs) == (
        "5a1d04cc59378b51256e715a054307d5f6fb442214cdbb0bed34e9f288c4fb7d"
    )
    assert outcome.version.plan_sha256 == (
        "c3260b7919229d473e737ca6d02bdd79274d10f94f24719004da56d70428d10a"
    )
    replayed = use_case.execute(inputs, plan_at=NOW)
    assert replayed.replayed
    assert replayed.replay_view is not None
    assert replayed.replay_view.plan_sha256 == outcome.version.plan_sha256
