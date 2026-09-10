"""M6 domain-artifact tests: determinism, clock exclusion, reason fidelity."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal

from nutrition_agent.domain.nutrition.serialization import to_json_bytes
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import (
    PlanRunStatus,
    plan_document,
    plan_reason_codes,
    plan_run_status_of,
)
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
from tests.unit.planning_helpers import OfferingViewArgs, make_offering  # noqa: F401

PLAN_DATE = date(2026, 8, 21)  # Friday
PLAN_AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
MENU_SHA = "artifact-tests-menu-sha"


def _schedule() -> WeeklySchedule:
    lunch_block = ScheduleBlock(
        kind=BlockKind.STACKS_MEAL,
        start=time(12, 30),
        end=time(13, 15),
        label="stacks post-workout lunch",
        meal_context=MealContext.POST_WORKOUT_LUNCH,
    )
    day = DaySchedule(weekday=Weekday.FRIDAY, blocks=(lunch_block,))
    return WeeklySchedule(
        version="artifact-test.v1",
        timezone="America/New_York",
        days={
            **{weekday: DaySchedule(weekday=weekday, blocks=()) for weekday in Weekday},
            Weekday.FRIDAY: day,
        },
    )


def _policy() -> PlannerPolicy:
    return PlannerPolicy(
        version="artifact-planner.v1",
        context_period={MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH},
        slot_shares={},
    )


def _slot_policies() -> dict[MealContext, SlotPolicy]:
    return {MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)}


def _targets(calories: str = "900") -> TargetSet:
    return TargetSet(
        policy_version="artifact-targets.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal(calories), weight=Decimal("1")
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal("50"), weight=Decimal("1")
            ),
        },
    )


def _menu(fetched_at: datetime | None = None, empty_lunch: bool = False) -> MenuDayView:
    if empty_lunch:
        lunch = PeriodMenu(offerings=(), explicitly_empty=True)
    else:
        first = make_offering(1).build()
        second = make_offering(2, calories="700").build()
        lunch = PeriodMenu(offerings=(first, second))
    return MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: lunch},
        fetched_at=fetched_at or datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
        snapshot_sha256=MENU_SHA,
    )


def _result(menu: MenuDayView | None = None, targets: TargetSet | None = None):
    from nutrition_agent.domain.planning.planner import generate_daily_plan

    return generate_daily_plan(
        plan_date=PLAN_DATE,
        plan_at=PLAN_AT,
        schedule=_schedule(),
        exceptions=(),
        menu=menu or _menu(),
        policy=_policy(),
        slot_policies=dict(_slot_policies()),
        targets=targets or _targets(),
    )


def _canonical(result) -> tuple[str, str]:
    raw = to_json_bytes(plan_document(result)).decode("utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    reparsed = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    assert reparsed == raw, "artifact must already be canonical before storage"
    return raw, digest


def test_identical_inputs_identical_artifact_and_hash() -> None:
    assert _canonical(_result()) == _canonical(_result())


def test_fetched_at_is_execution_metadata_excluded_from_artifact() -> None:
    drifted = datetime(2026, 8, 20, 23, 30, tzinfo=UTC)
    assert _canonical(_result(_menu(drifted))) == _canonical(_result())


def test_changed_target_changes_hash_with_structure_intact() -> None:
    base_raw, base_sha = _canonical(_result())
    adjusted = _targets(calories="1234")
    mod_raw, mod_sha = _canonical(_result(targets=adjusted))
    assert base_sha != mod_sha
    base_doc = json.loads(base_raw)
    mod_doc = json.loads(mod_raw)
    assert set(base_doc) == set(mod_doc)
    assert [s["status"] for s in base_doc["slots"]] == [s["status"] for s in mod_doc["slots"]]


def test_no_plan_empty_period_reason_codes_first_class_and_deterministic() -> None:
    result = _result(_menu(empty_lunch=True))
    assert plan_run_status_of(result) is PlanRunStatus.NO_PLAN
    assert plan_reason_codes(result) == ("empty_menu_period",)
    raw_a, sha_a = _canonical(result)
    raw_b, sha_b = _canonical(_result(_menu(empty_lunch=True)))
    assert raw_a == raw_b and sha_a == sha_b
    document = json.loads(raw_a)
    assert document["slots"][0]["failure_reasons"] == ["empty_menu_period"]
    assert document["status"] == "no_plan"


def test_completed_run_has_empty_run_level_reason_codes() -> None:
    result = _result()
    assert plan_run_status_of(result) is PlanRunStatus.COMPLETED
    assert plan_reason_codes(result) == ()


def test_zero_slot_reason_is_first_class_in_run_and_artifact() -> None:
    from nutrition_agent.domain.planning.eligibility import ReasonCode
    from nutrition_agent.domain.planning.planner import PlannerStatus, generate_daily_plan

    empty_schedule = WeeklySchedule(
        version="empty-schedule.v1",
        timezone="America/New_York",
        days={weekday: DaySchedule(weekday=weekday, blocks=()) for weekday in Weekday},
    )
    result = generate_daily_plan(
        plan_date=PLAN_DATE,
        plan_at=PLAN_AT,
        schedule=empty_schedule,
        exceptions=(),
        menu=_menu(),
        policy=_policy(),
        slot_policies=_slot_policies(),
        targets=_targets(),
    )

    assert result.status is PlannerStatus.NO_PLAN
    assert result.slots == ()
    assert result.failure_reasons == (ReasonCode.NO_RESOLVED_MEAL_SLOTS,)
    assert plan_reason_codes(result) == ("no_resolved_meal_slots",)
    document = plan_document(result)
    assert document["failure_reasons"] == ["no_resolved_meal_slots"]
