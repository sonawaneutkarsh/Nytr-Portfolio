"""Bridge tests: NutritionProfile -> NutritionFacts/MealLine from real fixtures.

Includes the three owner-required regression tests:
1. fixture 0 g trans fat -> quantities AND published_zero;
2. derived arithmetic zero never enters published_zero;
3. unavailable nutrients are never classified as published_zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.domain.nutrition.arithmetic import sum_facts
from nutrition_agent.domain.nutrition.facts import (
    ALL_NUTRIENT_KEYS,
    NutrientPresence,
    NutritionFacts,
    presence_of,
)
from nutrition_agent.domain.stacks.entities import (
    NUTRIENT_UNIT,
    Confidence,
    NutrientKey,
    NutrientValue,
    NutritionProfile,
    Provenance,
    ServingBasisKind,
)
from nutrition_agent.domain.stacks.facts_bridge import (
    UnusableProfileError,
    facts_from_profile,
    meal_line_from_profile,
)
from nutrition_agent.infrastructure.stacks_source.label_parser import LabelPageParser
from nutrition_agent.infrastructure.stacks_source.normalizer import profile_from_parsed

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"


def _provenance(sha: str = "deadbeef") -> Provenance:
    return Provenance(
        snapshot_id=UUID(int=1),
        content_sha256=sha,
        source_url="fixture://label",
        parser_version="2026-08-21.m2.1",
        fetched_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


@pytest.fixture
def chicken_profile() -> NutritionProfile:
    html = (FIXTURES / "nutrition_label_standard_roast_chicken.html").read_text(encoding="utf-8")
    parsed = LabelPageParser().parse(html)
    assert parsed.ok and parsed.value is not None
    return profile_from_parsed(parsed.value, UUID(int=1), _provenance())


# Regression 1: source-published zero lands in BOTH collections.
def test_fixture_trans_fat_is_quantified_and_published_zero(chicken_profile) -> None:  # type: ignore[no-untyped-def]
    facts = facts_from_profile(chicken_profile)
    assert facts.quantities[NutrientKey.TRANS_FAT_G] == Decimal("0")
    assert NutrientKey.TRANS_FAT_G in facts.published_zero
    assert presence_of(facts, NutrientKey.TRANS_FAT_G) is NutrientPresence.PUBLISHED_ZERO


# Regression 2: derived zero never becomes PUBLISHED_ZERO. Derived via a legal
# arithmetic path (sum of published-zero inputs); scale-by-zero is now forbidden
# at the domain boundary, so it can no longer produce derived facts at all.
def test_derived_arithmetic_zero_is_not_published_zero(chicken_profile) -> None:  # type: ignore[no-untyped-def]
    facts = facts_from_profile(chicken_profile)
    zeroed = NutritionFacts(
        quantities={key: Decimal("0") for key in facts.quantities},
        published_zero=frozenset(facts.quantities.keys()),
        declared_unavailable=frozenset(),
        confidence=facts.confidence,
    )
    summed = sum_facts([zeroed])
    for key in summed.quantities:
        assert presence_of(summed, key) is NutrientPresence.KNOWN_VALUE
    assert summed.published_zero == frozenset()
    assert summed.quantities[NutrientKey.CALORIES_KCAL] == Decimal("0")
    assert summed.quantities[NutrientKey.PROTEIN_G] == Decimal("0")


# Regression 3: unavailable fields never enter published_zero.
def test_declared_unavailable_is_not_published_zero() -> None:
    profile = NutritionProfile(
        food_id=UUID(int=2),
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
        nutrients={
            NutrientKey.CALORIES_KCAL: NutrientValue(
                value=Decimal(500), unit=NUTRIENT_UNIT[NutrientKey.CALORIES_KCAL], dv_percent=None
            )
        },
        unavailable_fields=(NutrientKey.FIBER_G,),
        extra_fields={},
        ingredients_raw="x",
        ingredient_components=None,
        allergens=(),
        confidence=Confidence.PARTIAL,
        provenance=_provenance("cafe"),
    )
    facts = facts_from_profile(profile)
    assert NutrientKey.FIBER_G not in facts.published_zero
    assert NutrientKey.FIBER_G in facts.declared_unavailable
    assert presence_of(facts, NutrientKey.FIBER_G) is NutrientPresence.DECLARED_UNAVAILABLE


def test_placeholder_label_yields_no_facts() -> None:
    html = (FIXTURES / "nutrition_label_placeholder_halal_bowl.html").read_text(encoding="utf-8")
    parsed = LabelPageParser().parse(html)
    assert parsed.ok and parsed.value is not None
    assert parsed.value.placeholder is True  # quarantined upstream; no profile exists


def test_all_15_nutrients_decimal_exact_against_expected_json(chicken_profile) -> None:  # type: ignore[no-untyped-def]
    facts = facts_from_profile(chicken_profile)
    expected: dict[str, str] = {
        "calories_kcal": "600",
        "total_fat_g": "20",
        "saturated_fat_g": "5",
        "trans_fat_g": "0",
        "cholesterol_mg": "100",
        "sodium_mg": "900",
        "vitamin_d_mcg": "2",
        "calcium_mg": "100",
        "carbohydrate_g": "50",
        "fiber_g": "6",
        "sugars_g": "8",
        "added_sugars_g": "2",
        "protein_g": "40",
        "iron_mg": "4",
        "potassium_mg": "700",
    }
    assert set(facts.quantities.keys()) == set(ALL_NUTRIENT_KEYS)
    for key_name, expected_str in expected.items():
        key = NutrientKey(key_name)
        assert facts.quantities[key] == Decimal(expected_str), key_name
    assert facts.confidence is Confidence.OFFICIAL_PUBLISHED
    assert facts.declared_unavailable == frozenset()


def test_meal_line_pins_versions_and_basis(chicken_profile) -> None:  # type: ignore[no-untyped-def]
    line = meal_line_from_profile(chicken_profile, Decimal(2))
    assert line.food_id == chicken_profile.food_id
    assert line.profile_content_sha256 == chicken_profile.provenance.content_sha256
    assert line.parser_version == chicken_profile.provenance.parser_version
    assert line.servings == Decimal(2)
    assert line.serving_basis_raw == chicken_profile.serving_basis_raw
    assert line.serving_basis_kind == ServingBasisKind.UNITLESS_SERVINGS


def test_meal_line_rejects_nonpositive_servings(chicken_profile) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="servings must be positive"):
        meal_line_from_profile(chicken_profile, Decimal(0))
    with pytest.raises(ValueError, match="servings must be positive"):
        meal_line_from_profile(chicken_profile, Decimal("-1"))


def test_partial_empty_profile_refused() -> None:
    empty = NutritionProfile(
        food_id=UUID(int=3),
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNKNOWN,
        nutrients={},
        unavailable_fields=(),
        extra_fields={},
        ingredients_raw="",
        ingredient_components=None,
        allergens=(),
        confidence=Confidence.PARTIAL,
        provenance=_provenance("empty"),
    )
    with pytest.raises(UnusableProfileError):
        facts_from_profile(empty)
