"""Decimal-only arithmetic over NutritionFacts (ADR-014).

Missing-value semantics: a nutrient is computable for a part only when present
in that part's ``quantities`` (known values and published zeros alike). If ANY
part lacks a computable value for a key, the result omits the key and records
DECLARED_UNAVAILABLE when any part declared it, else leaves it UNKNOWN_ABSENT.
Values are never zero-filled. Derived results always carry an empty
``published_zero``; derived exact zeros are plain KNOWN_VALUE(0).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from nutrition_agent.domain.nutrition.facts import (
    NutritionFacts,
    weakest_confidence,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


def scale_facts(facts: NutritionFacts, servings: Decimal) -> NutritionFacts:
    if servings <= 0:
        raise ValueError("servings must be positive")
    quantities = {key: value * servings for key, value in facts.quantities.items()}
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(),
        declared_unavailable=frozenset(facts.declared_unavailable),
        confidence=facts.confidence,
    )


def sum_facts(parts: Sequence[NutritionFacts]) -> NutritionFacts:
    if not parts:
        raise ValueError("sum_facts requires at least one part")
    computable: set[NutrientKey] = set(parts[0].quantities.keys())
    for part in parts[1:]:
        computable &= set(part.quantities.keys())
    declared: set[NutrientKey] = set()
    for part in parts:
        declared |= part.declared_unavailable
    quantities = {
        key: sum((part.quantities[key] for part in parts), Decimal(0)) for key in computable
    }
    confidence: Confidence = weakest_confidence(part.confidence for part in parts)
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(),
        declared_unavailable=frozenset(declared - computable),
        confidence=confidence,
    )
