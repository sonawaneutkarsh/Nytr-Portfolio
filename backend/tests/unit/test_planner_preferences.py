"""Production-v2 allocation and exact-alias soft-preference regressions."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast

import pytest

from nutrition_agent.application.server_inputs import (
    PRODUCTION_SERVER_CONFIGURATION_V1,
    PRODUCTION_SERVER_CONFIGURATION_V2,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.eligibility import ReasonCode, facts_of
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.planner import PlannerStatus, generate_daily_plan
from nutrition_agent.domain.planning.policy import OfferingPreference, PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import Confidence, MealPeriod, NutrientKey
from tests.unit.planning_helpers import make_offering

PLAN_DATE = date(2026, 8, 31)
PLAN_AT = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _schedule() -> WeeklySchedule:
    block = ScheduleBlock(
        kind=BlockKind.STACKS_MEAL,
        start=time(12),
        end=time(13),
        label="post-workout lunch",
        meal_context=MealContext.POST_WORKOUT_LUNCH,
    )
    return WeeklySchedule(
        version="preference-test-schedule.v1",
        timezone="America/New_York",
        days={
            weekday: DaySchedule(
                weekday=weekday,
                blocks=(block,) if weekday is Weekday.MONDAY else (),
            )
            for weekday in Weekday
        },
    )


def _targets() -> TargetSet:
    return TargetSet(
        policy_version="calorie-only-test.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET,
                value=Decimal("2400"),
                weight=Decimal("1"),
            )
        },
    )


def _result(
    offerings: tuple[OfferingView, ...],
    *,
    policy: PlannerPolicy | None = None,
):
    menu = MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=offerings)},
        fetched_at=PLAN_AT,
        snapshot_sha256="preference-test-menu",
    )
    selected_policy = policy or PRODUCTION_SERVER_CONFIGURATION_V2.planner_policy
    return generate_daily_plan(
        plan_date=PLAN_DATE,
        plan_at=PLAN_AT,
        schedule=_schedule(),
        exceptions=(),
        menu=menu,
        policy=selected_policy,
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=_targets(),
    )


def _offering(
    number: int,
    name: str,
    calories: str,
    *,
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
    complete: bool = True,
    with_profile: bool = True,
) -> OfferingView:
    return cast(
        OfferingView,
        make_offering(
            number,
            name=name,
            calories=calories,
            confidence=confidence,
            complete=complete,
            with_profile=with_profile,
        ).build(),
    )


def _top_names(result) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    return tuple(ref.name_normalized for ref in result.slots[0].candidates[0].line_refs)


def test_cyo_exact_alias_gets_highest_soft_bonus_when_strict() -> None:
    result = _result(
        (
            _offering(1, "CYO Halal Bowl", "900"),
            _offering(2, "Neutral Plate", "920"),
        )
    )

    top = result.slots[0].candidates[0]
    assert _top_names(result) == ("CYO Halal Bowl",)
    assert top.score_breakdown["preference:owner.cyo_halal_bowl"] == Decimal("0.05")
    assert top.score_total == Decimal("-0.0125")


def test_chicken_alias_beats_only_marginally_closer_neutral() -> None:
    result = _result(
        (
            _offering(10, "#7 Roast Chicken & Provolone", "900"),
            _offering(11, "Neutral Plate", "920"),
        )
    )

    top = result.slots[0].candidates[0]
    assert _top_names(result) == ("#7 Roast Chicken & Provolone",)
    assert top.score_breakdown["preference:owner.deli_chicken_provolone"] == Decimal("0.03")


def test_preference_cannot_overcome_clearly_worse_target_fit() -> None:
    result = _result(
        (
            _offering(20, "#7 Roast Chicken & Provolone", "700"),
            _offering(21, "Neutral Plate", "950"),
        )
    )

    assert _top_names(result) == ("Neutral Plate",)


def test_no_preference_retains_nutritional_and_deterministic_order() -> None:
    first = _offering(30, "Neutral 900", "900")
    second = _offering(31, "Neutral 950", "950")

    forward = _result((first, second))
    reverse = _result((second, first))

    assert _top_names(forward) == ("Neutral 950",)
    assert forward == reverse
    assert not any(
        key.startswith("preference:") for key in forward.slots[0].candidates[0].score_breakdown
    )


def test_useful_pair_still_beats_inadequate_singles() -> None:
    result = _result(
        (
            _offering(40, "Moderate A", "520"),
            _offering(41, "Moderate B", "410"),
        )
    )

    assert _top_names(result) == ("Moderate A", "Moderate B")


def test_v2_allocation_removes_full_day_incentive_for_two_large_entrees() -> None:
    offerings = (
        _offering(50, "Large Entrée A", "900"),
        _offering(51, "Large Entrée B", "850"),
    )

    old = _result(offerings, policy=PRODUCTION_SERVER_CONFIGURATION_V1.planner_policy)
    new = _result(offerings)

    assert _top_names(old) == ("Large Entrée A", "Large Entrée B")
    assert _top_names(new) == ("Large Entrée A",)


@pytest.mark.parametrize(
    "offering",
    [
        _offering(60, "CYO Halal Bowl", "900", with_profile=False),
        _offering(61, "CYO Halal Bowl", "900", confidence=Confidence.ESTIMATED),
        _offering(62, "CYO Halal Bowl", "900", complete=False),
    ],
)
def test_cyo_preference_never_bypasses_strict_eligibility(offering: OfferingView) -> None:
    neutral = _offering(63, "Neutral Strict Plate", "900")
    result = _result((offering, neutral))

    assert result.status is PlannerStatus.OK
    assert _top_names(result) == ("Neutral Strict Plate",)
    assert set(result.slots[0].failure_reasons) & {
        ReasonCode.NO_PROFILE_LINKED,
        ReasonCode.NOT_STRICT_ELIGIBLE,
    }


def test_exact_alias_matching_does_not_use_fuzzy_or_substring_matching() -> None:
    near_name = _offering(70, "CYO Halal Bowl Special", "900")
    neutral = _offering(71, "Neutral Plate", "920")

    result = _result((near_name, neutral))

    assert _top_names(result) == ("Neutral Plate",)
    near = next(
        candidate
        for candidate in result.slots[0].candidates
        if tuple(ref.name_normalized for ref in candidate.line_refs) == ("CYO Halal Bowl Special",)
    )
    assert not any(key.startswith("preference:") for key in near.score_breakdown)


def test_pair_receives_only_the_strongest_single_non_cumulative_bonus() -> None:
    result = _result(
        (
            _offering(80, "CYO Halal Bowl", "450"),
            _offering(81, "#7 Roast Chicken & Provolone", "450"),
        )
    )
    pair = next(
        candidate for candidate in result.slots[0].candidates if len(candidate.line_refs) == 2
    )

    preference_entries = {
        key: value for key, value in pair.score_breakdown.items() if key.startswith("preference:")
    }
    assert preference_entries == {"preference:owner.cyo_halal_bowl": Decimal("0.05")}


def test_preferred_entree_does_not_make_an_unnecessary_large_pair_win() -> None:
    result = _result(
        (
            _offering(90, "CYO Halal Bowl", "900"),
            _offering(91, "Large Neutral Entrée", "850"),
        )
    )

    assert _top_names(result) == ("CYO Halal Bowl",)


def test_incomplete_preferred_offering_keeps_missing_nutrients_unknown() -> None:
    incomplete = _offering(92, "CYO Halal Bowl", "900", complete=False)
    assert incomplete.profile is not None

    facts = facts_of(incomplete)

    assert facts.quantities[NutrientKey.CALORIES_KCAL] == Decimal("900")
    assert NutrientKey.PROTEIN_G not in facts.quantities
    assert NutrientKey.PROTEIN_G not in facts.published_zero


def test_offering_preference_requires_decimal_and_exact_normalized_aliases() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        OfferingPreference("bad", frozenset({"Exact"}), 0.05)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="whitespace-normalized"):
        OfferingPreference("bad", frozenset({" CYO Halal Bowl "}), Decimal("0.05"))
