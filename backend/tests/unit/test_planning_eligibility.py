"""Two-stage eligibility tests: every reason code, both stages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nutrition_agent.domain.planning.candidates import generate_candidates
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.eligibility import (
    ReasonCode,
    candidate_gate,
    freshness_gate,
    offering_gate,
    period_gate,
)
from nutrition_agent.domain.planning.menu_view import PeriodMenu
from nutrition_agent.domain.planning.policy import SlotPolicy
from nutrition_agent.domain.stacks.entities import Confidence, DietaryTag
from tests.unit.planning_helpers import make_offering

FRESH_AT = datetime(2026, 8, 24, 6, 0, tzinfo=UTC)
PLAN_AT = FRESH_AT + timedelta(hours=2)


def test_period_gate_distinguishes_data_gap_from_validated_empty() -> None:
    missing = period_gate(None)
    assert missing is not None and missing.reason is ReasonCode.MENU_DATA_UNAVAILABLE
    explicit = period_gate(PeriodMenu(offerings=(), explicitly_empty=True))
    assert explicit is not None and explicit.reason is ReasonCode.EMPTY_MENU_PERIOD
    populated = PeriodMenu(offerings=(make_offering(1).build(),))  # type: ignore[list-item]
    assert period_gate(populated) is None


def test_freshness_gate_fresh_passes_stale_refuses() -> None:
    assert freshness_gate(FRESH_AT, PLAN_AT, 20 * 3600) is None
    stale = freshness_gate(FRESH_AT, PLAN_AT + timedelta(hours=19), 20 * 3600)
    assert stale is not None and stale.reason is ReasonCode.MENU_STALE


def test_no_profile_linked_reason() -> None:
    offering = make_offering(3, with_profile=False).build()
    rejection = offering_gate(offering, SlotPolicy(context=MealContext.DINNER))  # type: ignore[arg-type]
    assert rejection is not None and rejection.reason is ReasonCode.NO_PROFILE_LINKED


def test_not_strict_eligible_for_partial_confidence() -> None:
    offering = make_offering(4, confidence=Confidence.PARTIAL).build()
    rejection = offering_gate(offering, SlotPolicy(context=MealContext.DINNER))  # type: ignore[arg-type]
    assert rejection is not None and rejection.reason is ReasonCode.NOT_STRICT_ELIGIBLE


def test_not_strict_eligible_for_incomplete_nutrients() -> None:
    offering = make_offering(5, complete=False).build()
    rejection = offering_gate(offering, SlotPolicy(context=MealContext.DINNER))  # type: ignore[arg-type]
    assert rejection is not None and rejection.reason is ReasonCode.NOT_STRICT_ELIGIBLE


def test_dietary_filter_reason() -> None:
    porky = make_offering(6, tags=(DietaryTag.CONTAINS_PORK,)).build()
    policy = SlotPolicy(context=MealContext.DINNER, exclude_tags=(DietaryTag.CONTAINS_PORK,))
    rejection = offering_gate(porky, policy)  # type: ignore[arg-type]
    assert rejection is not None and rejection.reason is ReasonCode.DIETARY_FILTER
    # without the filter it passes
    assert offering_gate(porky, SlotPolicy(context=MealContext.DINNER)) is None  # type: ignore[arg-type]


def test_eligible_offering_passes_all_gates() -> None:
    good = make_offering(8, calories="600").build()
    assert offering_gate(good, SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)) is None  # type: ignore[arg-type]


def _candidate_for(*numbers: int):
    offerings = tuple(make_offering(number).build() for number in numbers)  # type: ignore[list-item]
    generated = generate_candidates(offerings, max_candidates=50)
    return generated


def test_candidate_calorie_bounds_rejects_composed_pair() -> None:
    # parts are individually within [500, 900]; the pair composes to 1600.
    a = make_offering(10, calories="800")
    b = make_offering(11, calories="800")
    candidates = {
        candidate.sort_key: candidate
        for candidate in generate_candidates((a.build(), b.build()), 50)  # type: ignore[arg-type,list-item]
    }
    pair_keys = [key for key in candidates if len(candidates[key].lines) == 2]
    assert len(pair_keys) == 1
    pair = candidates[pair_keys[0]]
    policy = SlotPolicy(
        context=MealContext.DINNER, calorie_min=Decimal(500), calorie_max=Decimal(900)
    )
    singles_ok = [candidate for candidate in candidates.values() if len(candidate.lines) == 1]
    for single in singles_ok:
        assert candidate_gate(single.meal, policy) is None
    rejection = candidate_gate(pair.meal, policy)
    assert rejection is not None
    assert rejection.reason is ReasonCode.CANDIDATE_CALORIE_BOUNDS
    assert "1600" in rejection.detail


def test_candidate_bounds_accept_within_range() -> None:
    a = make_offering(12, calories="300")
    policy = SlotPolicy(
        context=MealContext.DINNER,
        calorie_min=Decimal("200"),
        calorie_max=Decimal("700"),
    )
    candidates = generate_candidates((a.build(),), 10)  # type: ignore[arg-type,list-item]
    assert all(candidate_gate(candidate.meal, policy) is None for candidate in candidates)


def test_candidate_not_strict_defensive_check() -> None:
    from nutrition_agent.domain.nutrition.facts import NutritionFacts
    from nutrition_agent.domain.nutrition.meal import ComposedMeal
    from nutrition_agent.domain.stacks.entities import Confidence as Conf

    incomplete = NutritionFacts(
        quantities={},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Conf.OFFICIAL_PUBLISHED,
    )
    meal = ComposedMeal(
        lines=(),
        totals=incomplete,
        confidence=Conf.ESTIMATED,
        engine_policy_version="test",
    )
    rejection = candidate_gate(meal, SlotPolicy(context=MealContext.DINNER))
    assert rejection is not None
    assert rejection.reason is ReasonCode.CANDIDATE_NOT_STRICT
