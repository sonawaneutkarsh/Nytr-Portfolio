"""Daily totals: ordered fold of composed meals."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.meal import MealLine, compose_meal
from nutrition_agent.domain.nutrition.totals import sum_daily
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    NutrientKey,
    ServingBasisKind,
)


def _line(food_number: int, calories: str) -> MealLine:
    return MealLine(
        food_id=UUID(int=food_number),
        profile_content_sha256=f"sha-{food_number}",
        parser_version="2026-08-21.m2.1",
        servings=Decimal(1),
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
        facts=NutritionFacts(
            quantities={NutrientKey.CALORIES_KCAL: Decimal(calories)},
            published_zero=frozenset(),
            declared_unavailable=frozenset(),
            confidence=Confidence.OFFICIAL_PUBLISHED,
        ),
    )


def test_day_total_equals_ordered_fold() -> None:
    lunch = compose_meal([_line(1, "1318"), _line(2, "200")])
    dinner = compose_meal([_line(3, "700")])
    day = sum_daily([lunch, dinner])
    assert day.totals is not None
    assert day.totals.quantities[NutrientKey.CALORIES_KCAL] == Decimal("2218")
    assert len(day.meals) == 2
    # meal boundaries preserved
    assert [len(meal.lines) for meal in day.meals] == [2, 1]


def test_empty_day_is_allowed_with_no_totals() -> None:
    day = sum_daily([])
    assert day.totals is None
    assert day.meals == ()


def test_day_totals_carry_no_published_zero_and_policy_version() -> None:
    from nutrition_agent.domain.nutrition import ENGINE_POLICY_VERSION

    line = MealLine(
        food_id=UUID(int=9),
        profile_content_sha256="sha",
        parser_version="2026-08-21.m2.1",
        servings=Decimal(1),
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
        facts=NutritionFacts(
            quantities={NutrientKey.TRANS_FAT_G: Decimal("0")},
            published_zero=frozenset({NutrientKey.TRANS_FAT_G}),
            declared_unavailable=frozenset(),
            confidence=Confidence.OFFICIAL_PUBLISHED,
        ),
    )
    day = sum_daily([compose_meal([line])])
    assert day.totals is not None
    assert day.totals.published_zero == frozenset()
    assert day.engine_policy_version == ENGINE_POLICY_VERSION
