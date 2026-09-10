"""Four-state NutrientPresence semantics: precedence + construction invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest

from nutrition_agent.domain.nutrition.facts import (
    NutrientPresence,
    NutritionFacts,
    presence_of,
    weakest_confidence,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


def _facts(
    quantities: dict[NutrientKey, Decimal] | None = None,
    published_zero: frozenset[NutrientKey] = frozenset(),
    declared_unavailable: frozenset[NutrientKey] = frozenset(),
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
) -> NutritionFacts:
    return NutritionFacts(
        quantities=quantities or {},
        published_zero=published_zero,
        declared_unavailable=declared_unavailable,
        confidence=confidence,
    )


def test_published_zero_key_classifies_as_published_even_also_in_quantities() -> None:
    facts = _facts({NutrientKey.TRANS_FAT_G: Decimal(0)}, frozenset({NutrientKey.TRANS_FAT_G}))
    assert presence_of(facts, NutrientKey.TRANS_FAT_G) is NutrientPresence.PUBLISHED_ZERO


def test_nonzero_quantity_classifies_as_known_value() -> None:
    facts = _facts({NutrientKey.PROTEIN_G: Decimal("69.2")})
    assert presence_of(facts, NutrientKey.PROTEIN_G) is NutrientPresence.KNOWN_VALUE


def test_declared_and_absent_states() -> None:
    facts = _facts(declared_unavailable=frozenset({NutrientKey.FIBER_G}))
    assert presence_of(facts, NutrientKey.FIBER_G) is NutrientPresence.DECLARED_UNAVAILABLE
    assert presence_of(facts, NutrientKey.IRON_MG) is NutrientPresence.UNKNOWN_ABSENT


def test_known_value_zero_without_published_marker_is_known() -> None:
    facts = _facts({NutrientKey.IRON_MG: Decimal("0")})
    assert facts.published_zero == frozenset()
    assert presence_of(facts, NutrientKey.IRON_MG) is NutrientPresence.KNOWN_VALUE


def test_invariant_published_zero_must_be_subset_of_quantities() -> None:
    with pytest.raises(ValueError, match="published_zero must be a subset"):
        _facts({}, published_zero=frozenset({NutrientKey.TRANS_FAT_G}))


def test_invariant_quantities_and_declared_disjoint() -> None:
    with pytest.raises(ValueError, match="quantities and declared_unavailable"):
        _facts(
            {NutrientKey.FIBER_G: Decimal("2.5")},
            declared_unavailable=frozenset({NutrientKey.FIBER_G}),
        )


def test_invariant_published_zero_and_declared_disjoint() -> None:
    with pytest.raises(ValueError):
        NutritionFacts(
            quantities={NutrientKey.TRANS_FAT_G: Decimal(0)},
            published_zero=frozenset({NutrientKey.TRANS_FAT_G}),
            declared_unavailable=frozenset({NutrientKey.TRANS_FAT_G}),
            confidence=Confidence.PARTIAL,
        )


def test_frozen_dataclass_rejects_mutation() -> None:
    facts = _facts({NutrientKey.CALORIES_KCAL: Decimal(1318)})
    with pytest.raises(AttributeError):
        facts.confidence = Confidence.ESTIMATED  # type: ignore[misc]


def test_weakest_confidence_ordering() -> None:
    assert (
        weakest_confidence([Confidence.OFFICIAL_PUBLISHED, Confidence.ESTIMATED])
        is Confidence.ESTIMATED
    )
    assert (
        weakest_confidence([Confidence.OFFICIAL_COMPONENT_SUM, Confidence.VERIFIED_INTERNAL_RECIPE])
        is Confidence.VERIFIED_INTERNAL_RECIPE
    )
    assert weakest_confidence([Confidence.OFFICIAL_PUBLISHED]) is Confidence.OFFICIAL_PUBLISHED
