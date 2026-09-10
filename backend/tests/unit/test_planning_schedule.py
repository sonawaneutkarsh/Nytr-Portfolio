"""Schedule domain tests: data-driven resolution, exceptions, home-meal exclusion."""

from __future__ import annotations

from datetime import date, time

import pytest

from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    ScheduleBlock,
    ScheduleException,
    Weekday,
    resolve_day,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from tests.unit.planning_helpers import (
    PLAN_DATE_MONDAY,
    PLAN_DATE_TUESDAY,
    block,
    real_weekly_schedule,
)

POLICY_PERIODS = {
    MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH,
    MealContext.DINNER: MealPeriod.DINNER,
}


def test_block_validation() -> None:
    with pytest.raises(ValueError, match="before end"):
        ScheduleBlock(kind=BlockKind.WORKOUT, start=time(12, 0), end=time(12, 0), label="x")
    with pytest.raises(ValueError, match="requires a meal_context"):
        ScheduleBlock(kind=BlockKind.STACKS_MEAL, start=time(12, 0), end=time(13, 0), label="lunch")
    with pytest.raises(ValueError, match="only STACKS_MEAL"):
        ScheduleBlock(
            kind=BlockKind.WORKOUT,
            start=time(12, 0),
            end=time(13, 0),
            label="gym",
            meal_context=MealContext.DINNER,
        )


def test_monday_resolves_lunch_and_dinner_slots() -> None:
    slots = resolve_day(real_weekly_schedule(), (), PLAN_DATE_MONDAY, POLICY_PERIODS)
    contexts = [slot.context for slot in slots]
    assert contexts == [MealContext.POST_WORKOUT_LUNCH, MealContext.DINNER]
    lunch = slots[0]
    assert lunch.menu_period.value == "Lunch"
    assert lunch.window == (time(10, 15), time(11, 0))
    dinner = slots[1]
    assert dinner.menu_period.value == "Dinner"
    assert dinner.window == (time(19, 0), time(19, 45))


def test_tuesday_has_only_lunch_slot() -> None:
    slots = resolve_day(real_weekly_schedule(), (), PLAN_DATE_TUESDAY, POLICY_PERIODS)
    assert [slot.context for slot in slots] == [MealContext.POST_WORKOUT_LUNCH]
    assert slots[0].window == (time(10, 45), time(11, 30))


def test_thursday_rest_day_has_no_stacks_slots_but_keeps_commitment_block() -> None:
    thursday = date(2026, 8, 27)
    schedule = real_weekly_schedule()
    assert resolve_day(schedule, (), thursday, POLICY_PERIODS) == ()
    maep = schedule.days[Weekday.THURSDAY].blocks
    assert len(maep) == 1
    assert maep[0].kind is BlockKind.FIXED_COMMITMENT


def test_home_pre_workout_never_becomes_a_stacks_slot() -> None:
    schedule = real_weekly_schedule()
    for day_schedule in schedule.days.values():
        for schedule_block in day_schedule.blocks:
            if schedule_block.kind is BlockKind.HOME_PRE_WORKOUT_MEAL:
                assert schedule_block.meal_context is None
    monday_slots = resolve_day(schedule, (), PLAN_DATE_MONDAY, POLICY_PERIODS)
    assert all(slot.context is not None for slot in monday_slots)
    assert len(monday_slots) == 2


def test_exception_overrides_weekly_day() -> None:
    holiday = ScheduleException(date=PLAN_DATE_MONDAY, version="holiday-2026", day=None)
    slots = resolve_day(real_weekly_schedule(), (holiday,), PLAN_DATE_MONDAY, POLICY_PERIODS)
    assert slots == ()


def test_exception_with_custom_day_replaces_weekly_blocks() -> None:
    custom_day = type(real_weekly_schedule().days[Weekday.MONDAY])(
        weekday=Weekday.MONDAY,
        blocks=(
            block(
                BlockKind.STACKS_MEAL,
                "18:00",
                "18:30",
                "special dinner",
                context=MealContext.DINNER,
            ),
        ),
    )
    exception = ScheduleException(date=PLAN_DATE_MONDAY, version="move-dinner.v2", day=custom_day)
    slots = resolve_day(real_weekly_schedule(), (exception,), PLAN_DATE_MONDAY, POLICY_PERIODS)
    assert [slot.context for slot in slots] == [MealContext.DINNER]
    assert slots[0].window == (time(18, 0), time(18, 30))


def test_latest_exception_version_wins() -> None:
    v1_day = None
    v2_day = real_weekly_schedule().days[Weekday.MONDAY]
    exceptions = (
        ScheduleException(date=PLAN_DATE_MONDAY, version="a-v1", day=v1_day),
        ScheduleException(date=PLAN_DATE_MONDAY, version="b-v2", day=v2_day),
    )
    slots = resolve_day(real_weekly_schedule(), exceptions, PLAN_DATE_MONDAY, POLICY_PERIODS)
    assert [slot.context for slot in slots] == [
        MealContext.POST_WORKOUT_LUNCH,
        MealContext.DINNER,
    ]


def test_saturday_sunday_flexible_no_slots() -> None:
    schedule = real_weekly_schedule()
    saturday = date(2026, 8, 29)
    sunday = date(2026, 8, 30)
    assert resolve_day(schedule, (), saturday, POLICY_PERIODS) == ()
    assert resolve_day(schedule, (), sunday, POLICY_PERIODS) == ()
