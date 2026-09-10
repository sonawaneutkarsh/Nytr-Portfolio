"""Core nutrition facts model: four-state nutrient semantics (ADR-014).

Per-nutrient states are structurally distinct:

- KNOWN_VALUE: exact Decimal present in ``quantities``
- PUBLISHED_ZERO: source-published exact zero (also stored in ``quantities``)
- DECLARED_UNAVAILABLE: source rendered the field unavailable for the item
- UNKNOWN_ABSENT: the key appears in none of the collections

Derived facts produced by arithmetic always carry an empty ``published_zero``;
source-level zero provenance remains auditable through pinned references.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


class NutrientPresence(StrEnum):
    KNOWN_VALUE = "known_value"
    PUBLISHED_ZERO = "published_zero"
    DECLARED_UNAVAILABLE = "declared_unavailable"
    UNKNOWN_ABSENT = "unknown_absent"


ALL_NUTRIENT_KEYS: frozenset[NutrientKey] = frozenset(NutrientKey)

_CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.ESTIMATED: 0,
    Confidence.PARTIAL: 1,
    Confidence.VERIFIED_INTERNAL_RECIPE: 2,
    Confidence.OFFICIAL_COMPONENT_SUM: 3,
    Confidence.OFFICIAL_PUBLISHED: 4,
}

_STRICT_CONFIDENCES: frozenset[Confidence] = frozenset(
    {Confidence.OFFICIAL_PUBLISHED, Confidence.OFFICIAL_COMPONENT_SUM}
)


@dataclass(frozen=True)
class NutritionFacts:
    quantities: Mapping[NutrientKey, Decimal]
    published_zero: frozenset[NutrientKey]
    declared_unavailable: frozenset[NutrientKey]
    confidence: Confidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantities", MappingProxyType(dict(self.quantities)))
        object.__setattr__(self, "published_zero", frozenset(self.published_zero))
        object.__setattr__(self, "declared_unavailable", frozenset(self.declared_unavailable))
        quantity_keys = set(self.quantities.keys())
        if not self.published_zero <= quantity_keys:
            raise ValueError("published_zero must be a subset of quantities keys")
        if self.published_zero & self.declared_unavailable:
            raise ValueError("published_zero and declared_unavailable must be disjoint")
        if quantity_keys & self.declared_unavailable:
            raise ValueError("quantities and declared_unavailable must be disjoint")


def presence_of(facts: NutritionFacts, key: NutrientKey) -> NutrientPresence:
    if key in facts.published_zero:
        return NutrientPresence.PUBLISHED_ZERO
    if key in facts.quantities:
        return NutrientPresence.KNOWN_VALUE
    if key in facts.declared_unavailable:
        return NutrientPresence.DECLARED_UNAVAILABLE
    return NutrientPresence.UNKNOWN_ABSENT


def weakest_confidence(confidences: Iterable[Confidence]) -> Confidence:
    items = list(confidences)
    if not items:
        raise ValueError("cannot determine weakest confidence of an empty sequence")
    return min(items, key=lambda confidence: _CONFIDENCE_RANK[confidence])


def is_strict_eligible(facts: NutritionFacts) -> bool:
    return (
        facts.confidence in _STRICT_CONFIDENCES
        and not facts.declared_unavailable
        and set(facts.quantities.keys()) == set(ALL_NUTRIENT_KEYS)
    )
