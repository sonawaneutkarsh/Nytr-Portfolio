"""Daily totals: ordered fold of composed meals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nutrition_agent.domain.nutrition import ENGINE_POLICY_VERSION
from nutrition_agent.domain.nutrition.arithmetic import sum_facts
from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.meal import ComposedMeal


@dataclass(frozen=True)
class DailyTotals:
    meals: tuple[ComposedMeal, ...]
    totals: NutritionFacts | None
    engine_policy_version: str


def sum_daily(meals: Sequence[ComposedMeal]) -> DailyTotals:
    meal_tuple = tuple(meals)
    folded = sum_facts([meal.totals for meal in meal_tuple]) if meal_tuple else None
    return DailyTotals(
        meals=meal_tuple,
        totals=folded,
        engine_policy_version=ENGINE_POLICY_VERSION,
    )
