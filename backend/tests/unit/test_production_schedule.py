"""Production owner schedule and Sunday planning regression tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast

import pytest

from nutrition_agent.application.server_inputs import (
    CANONICAL_M6_DEMO_CONFIGURATION,
    PRODUCTION_SERVER_CONFIGURATION,
    PRODUCTION_SERVER_CONFIGURATION_V1,
    PRODUCTION_SERVER_CONFIGURATION_V2,
    PRODUCTION_SERVER_CONFIGURATION_V3,
    PRODUCTION_SERVER_CONFIGURATION_V4,
    required_menu_periods,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.eligibility import ReasonCode, offering_gate
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.planner import PlannerStatus, generate_daily_plan
from nutrition_agent.domain.planning.schedule import BlockKind, resolve_day
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    MealPeriod,
    NutrientKey,
    NutritionSourceState,
)
from tests.unit.planning_helpers import make_offering


@pytest.mark.parametrize(
    ("plan_date", "contexts", "periods", "windows"),
    [
        (
            date(2026, 8, 24),
            (MealContext.POST_WORKOUT_LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(10, 15), time(11)), (time(19), time(19, 45))),
        ),
        (
            date(2026, 8, 25),
            (MealContext.POST_WORKOUT_LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(10, 45), time(11, 30)), (time(19), time(19, 45))),
        ),
        (
            date(2026, 8, 26),
            (MealContext.POST_WORKOUT_LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(10, 15), time(11)), (time(19), time(19, 45))),
        ),
        (
            date(2026, 8, 27),
            (MealContext.LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(14), time(16)), (time(16), time(22))),
        ),
        (
            date(2026, 8, 28),
            (MealContext.POST_WORKOUT_LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(10, 15), time(11)), (time(19), time(19, 45))),
        ),
        (
            date(2026, 8, 29),
            (MealContext.LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(11), time(16)), (time(16), time(22))),
        ),
        (
            date(2026, 8, 30),
            (MealContext.LUNCH, MealContext.DINNER),
            (MealPeriod.LUNCH, MealPeriod.DINNER),
            ((time(11), time(16)), (time(16), time(22))),
        ),
    ],
)
def test_production_schedule_resolves_owner_stacks_slots(
    plan_date: date,
    contexts: tuple[MealContext, ...],
    periods: tuple[MealPeriod, ...],
    windows: tuple[tuple[time, time], ...],
) -> None:
    config = PRODUCTION_SERVER_CONFIGURATION

    slots = resolve_day(
        config.schedule,
        config.schedule_exceptions,
        plan_date,
        config.planner_policy.context_period,
    )

    assert tuple(slot.context for slot in slots) == contexts
    assert tuple(slot.menu_period for slot in slots) == periods
    assert tuple(slot.window for slot in slots) == windows


def test_production_configuration_is_not_the_frozen_demo() -> None:
    production = PRODUCTION_SERVER_CONFIGURATION

    assert production.schedule.version == "psh-fall-2026.v1"
    assert production.planner_policy.version == "psh-fall-2026-planner.v5"
    assert production.schedule is not CANONICAL_M6_DEMO_CONFIGURATION.schedule
    assert production.schedule.version != "m6-demo.v1"
    assert production.schedule.version != "weekly-test.v1"
    assert production.schedule_exceptions == ()
    assert production.planner_policy.context_period == {
        MealContext.LUNCH: MealPeriod.LUNCH,
        MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH,
        MealContext.DINNER: MealPeriod.DINNER,
    }
    assert production.slot_policies[MealContext.DINNER].portable_preferred is True


def test_production_menu_readiness_requires_only_reachable_stacks_periods() -> None:
    assert required_menu_periods(PRODUCTION_SERVER_CONFIGURATION) == (
        MealPeriod.LUNCH,
        MealPeriod.DINNER,
    )
    assert MealPeriod.BREAKFAST not in required_menu_periods(PRODUCTION_SERVER_CONFIGURATION)


def test_production_v5_copies_v4_and_preserves_v1_v2_v3_v4() -> None:
    current = PRODUCTION_SERVER_CONFIGURATION.planner_policy
    v4 = PRODUCTION_SERVER_CONFIGURATION_V4.planner_policy
    v3 = PRODUCTION_SERVER_CONFIGURATION_V3.planner_policy
    v2 = PRODUCTION_SERVER_CONFIGURATION_V2.planner_policy
    historical = PRODUCTION_SERVER_CONFIGURATION_V1.planner_policy

    assert current.slot_shares == {
        MealContext.LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.35")},
        MealContext.POST_WORKOUT_LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
        MealContext.DINNER: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
    }
    assert v2.version == "psh-fall-2026-planner.v2"
    assert v2.slot_shares == current.slot_shares
    assert v2.offering_preferences == current.offering_preferences
    assert v2.estimated_meal_policy is None
    assert v2.dietary_policy is None
    assert v3.version == "psh-fall-2026-planner.v3"
    assert v3.slot_shares == current.slot_shares
    assert v3.offering_preferences == current.offering_preferences
    assert v3.estimated_meal_policy is not None
    assert v3.dietary_policy is None
    assert v4.version == "psh-fall-2026-planner.v4"
    assert v4.slot_shares == current.slot_shares
    assert v4.offering_preferences == current.offering_preferences
    assert v4.estimated_meal_policy == current.estimated_meal_policy
    assert v4.dietary_policy is not None
    assert v4.dietary_policy.version == "owner-no-beef-pork.v1"
    assert current.estimated_meal_policy is not None
    assert current.dietary_policy is not None
    assert current.dietary_policy.version == "owner-chicken-seafood-veg.v1"
    assert historical.version == "psh-fall-2026-planner.v1"
    assert historical.slot_shares == {}
    assert historical.offering_preferences == ()


def test_home_pre_workout_breakfast_never_resolves_as_a_stacks_slot() -> None:
    config = PRODUCTION_SERVER_CONFIGURATION
    home_blocks = [
        block
        for day in config.schedule.days.values()
        for block in day.blocks
        if block.kind is BlockKind.HOME_PRE_WORKOUT_MEAL
    ]

    assert home_blocks
    assert all(block.meal_context is None for block in home_blocks)
    for plan_date in (date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)):
        slots = resolve_day(
            config.schedule,
            config.schedule_exceptions,
            plan_date,
            config.planner_policy.context_period,
        )
        assert all(slot.menu_period is not MealPeriod.BREAKFAST for slot in slots)


def _period(*, first: int, total: int, profiled: int, strict: int) -> PeriodMenu:
    offerings: list[OfferingView] = []
    for offset in range(total):
        has_profile = offset < profiled
        source_state = (
            NutritionSourceState.PROFILE_AVAILABLE
            if has_profile
            else (
                NutritionSourceState.SOURCE_PLACEHOLDER
                if offset < profiled + 2
                else NutritionSourceState.SOURCE_INCOMPLETE
            )
        )
        offering = cast(
            OfferingView,
            make_offering(
                first + offset,
                confidence=(
                    Confidence.OFFICIAL_PUBLISHED if offset < strict else Confidence.PARTIAL
                ),
                with_profile=has_profile,
            ).build(),
        )
        offerings.append(
            replace(
                offering,
                nutrition_source_state=source_state,
                nutrition_snapshot_sha256=f"nutrition-sha-{first + offset}",
            )
        )
    return PeriodMenu(offerings=tuple(offerings))


def _targets() -> TargetSet:
    return TargetSet(
        policy_version="sunday-authoritative-shaped.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET,
                value=Decimal("3000"),
                weight=Decimal("1"),
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=GoalKind.FLOOR,
                value=Decimal("150"),
                weight=Decimal("1"),
            ),
        },
    )


def test_sunday_authoritative_shaped_menu_reaches_candidates_and_plans() -> None:
    plan_date = date(2026, 8, 30)
    config = PRODUCTION_SERVER_CONFIGURATION
    lunch = _period(first=1_000, total=66, profiled=63, strict=58)
    dinner = _period(first=2_000, total=59, profiled=56, strict=53)
    menu = MenuDayView(
        service_date=plan_date,
        periods={
            MealPeriod.BREAKFAST: PeriodMenu(offerings=(), explicitly_empty=True),
            MealPeriod.LUNCH: lunch,
            MealPeriod.DINNER: dinner,
        },
        fetched_at=datetime(2026, 8, 30, 10, tzinfo=UTC),
        snapshot_sha256="authoritative-2026-08-30-shaped",
    )

    eligible_lunch = tuple(
        offering
        for offering in lunch.offerings
        if offering_gate(offering, config.slot_policies[MealContext.LUNCH]) is None
    )
    eligible_dinner = tuple(
        offering
        for offering in dinner.offerings
        if offering_gate(offering, config.slot_policies[MealContext.DINNER]) is None
    )
    result = generate_daily_plan(
        plan_date=plan_date,
        plan_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
        schedule=config.schedule,
        exceptions=config.schedule_exceptions,
        menu=menu,
        policy=config.planner_policy,
        slot_policies=config.slot_policies,
        targets=_targets(),
    )

    assert len(lunch.offerings) == 66
    assert len(dinner.offerings) == 59
    assert len(eligible_lunch) == 58
    assert len(eligible_dinner) == 53
    assert len(eligible_lunch) + len(eligible_lunch) * (len(eligible_lunch) - 1) // 2 == 1_711
    assert len(eligible_dinner) + len(eligible_dinner) * (len(eligible_dinner) - 1) // 2 == 1_431
    assert result.status is PlannerStatus.OK
    assert result.failure_reasons == ()
    assert tuple(slot.context for slot in result.slots) == (
        MealContext.LUNCH,
        MealContext.DINNER,
    )
    assert tuple(len(slot.candidates) for slot in result.slots) == (200, 200)
    assert tuple(dict(slot.rejection_counts) for slot in result.slots) == (
        {
            ReasonCode.NOT_STRICT_ELIGIBLE.value: 5,
            ReasonCode.NO_PROFILE_LINKED.value: 3,
        },
        {
            ReasonCode.NOT_STRICT_ELIGIBLE.value: 3,
            ReasonCode.NO_PROFILE_LINKED.value: 3,
        },
    )
