"""Bridge: Stacks ingestion profiles to nutrition-engine facts (ADR-014).

Pure domain adapters; no infrastructure imports. Placeholder/quarantined labels
never become NutritionProfile values upstream, so quarantined data is
unrepresentable as engine input; this bridge adds defense-in-depth refusals.
"""

from __future__ import annotations

from decimal import Decimal

from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.meal import MealLine
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey, NutritionProfile


class UnusableProfileError(ValueError):
    pass


def facts_from_profile(profile: NutritionProfile) -> NutritionFacts:
    quantities: dict[NutrientKey, Decimal] = {}
    published_zero: set[NutrientKey] = set()
    for key, nutrient_value in profile.nutrients.items():
        value = nutrient_value.value
        if value is None:
            continue
        quantities[key] = value
        if value == 0:
            published_zero.add(key)
    unavailable = frozenset(profile.unavailable_fields) - set(quantities.keys())
    if not quantities and profile.confidence is Confidence.PARTIAL:
        raise UnusableProfileError("profile carries no usable values and partial confidence")
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(published_zero),
        declared_unavailable=unavailable,
        confidence=profile.confidence,
    )


def meal_line_from_profile(profile: NutritionProfile, servings: Decimal) -> MealLine:
    if servings <= 0:
        raise ValueError("servings must be positive")
    return MealLine(
        food_id=profile.food_id,
        profile_content_sha256=profile.provenance.content_sha256,
        parser_version=profile.provenance.parser_version,
        servings=servings,
        serving_basis_raw=profile.serving_basis_raw,
        serving_basis_kind=profile.serving_basis_kind,
        facts=facts_from_profile(profile),
    )
