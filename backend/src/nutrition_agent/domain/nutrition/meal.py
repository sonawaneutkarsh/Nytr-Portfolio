"""Meal composition: independently scaled lines summed into derived totals.

Each MealLine is scaled against its OWN published serving basis; no cross-line
basis equality, unit conversion, or mass assumption exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from nutrition_agent.domain.nutrition import ENGINE_POLICY_VERSION
from nutrition_agent.domain.nutrition.arithmetic import scale_facts, sum_facts
from nutrition_agent.domain.nutrition.facts import NutritionFacts, weakest_confidence
from nutrition_agent.domain.stacks.entities import Confidence, ServingBasisKind


class EmptyMealError(ValueError):
    pass


@dataclass(frozen=True)
class MealLine:
    food_id: UUID
    profile_content_sha256: str
    parser_version: str
    servings: Decimal
    serving_basis_raw: str
    serving_basis_kind: ServingBasisKind
    facts: NutritionFacts

    def __post_init__(self) -> None:
        if self.servings <= 0:
            raise ValueError("servings must be positive")


@dataclass(frozen=True)
class ComposedMeal:
    lines: tuple[MealLine, ...]
    totals: NutritionFacts
    confidence: Confidence
    engine_policy_version: str


def compose_meal(lines: Sequence[MealLine]) -> ComposedMeal:
    if not lines:
        raise EmptyMealError("a meal requires at least one line")
    line_tuple = tuple(lines)
    totals = sum_facts([scale_facts(line.facts, line.servings) for line in line_tuple])
    return ComposedMeal(
        lines=line_tuple,
        totals=totals,
        confidence=weakest_confidence(line.facts.confidence for line in line_tuple),
        engine_policy_version=ENGINE_POLICY_VERSION,
    )
