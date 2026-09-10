"""Meal composition and daily totals: per-line scaling, mixed bases, weakest link."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.domain.nutrition import ENGINE_POLICY_VERSION
from nutrition_agent.domain.nutrition.facts import (
    NutrientPresence,
    NutritionFacts,
    presence_of,
)
from nutrition_agent.domain.nutrition.meal import EmptyMealError, MealLine, compose_meal
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey, ServingBasisKind


def _facts(
    values: dict[NutrientKey, str],
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
) -> NutritionFacts:
    quantities = {key: Decimal(v) for key, v in values.items()}
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(key for key, v in values.items() if Decimal(v) == 0),
        declared_unavailable=frozenset(),
        confidence=confidence,
    )


def _line(
    food_number: int,
    servings: str,
    basis_raw: str,
    values: dict[NutrientKey, str],
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
) -> MealLine:
    kind = ServingBasisKind.UNITLESS_SERVINGS if "SERVG" in basis_raw else ServingBasisKind.UNKNOWN
    return MealLine(
        food_id=UUID(int=food_number),
        profile_content_sha256=f"sha-{food_number}",
        parser_version="2026-08-21.m2.1",
        servings=Decimal(servings),
        serving_basis_raw=basis_raw,
        serving_basis_kind=kind,
        facts=_facts(values, confidence),
    )


def _chicken_values() -> dict[NutrientKey, str]:
    return {
        NutrientKey.CALORIES_KCAL: "1318",
        NutrientKey.PROTEIN_G: "69.2",
        NutrientKey.SODIUM_MG: "3769.1",
    }


def test_compose_scales_each_line_independently() -> None:
    line = _line(1, "2", "1 SERVG", _chicken_values())
    meal = compose_meal([line])
    assert meal.totals.quantities[NutrientKey.CALORIES_KCAL] == Decimal("2636")
    assert meal.totals.quantities[NutrientKey.PROTEIN_G] == Decimal("138.4")
    assert meal.confidence is Confidence.OFFICIAL_PUBLISHED
    assert meal.engine_policy_version == ENGINE_POLICY_VERSION


def test_mixed_serving_bases_compose_without_error() -> None:
    line_a = _line(1, "2", "1 SERVG", _chicken_values())
    line_b = _line(2, "1", "1 each (different label)", {NutrientKey.CALORIES_KCAL: "200"})
    meal = compose_meal([line_a, line_b])
    assert meal.totals.quantities[NutrientKey.CALORIES_KCAL] == Decimal("2836")
    # each line retains its own basis
    bases = {line.serving_basis_raw for line in meal.lines}
    assert bases == {"1 SERVG", "1 each (different label)"}


def test_duplicate_food_is_two_independent_lines() -> None:
    line = _line(1, "1", "1 SERVG", _chicken_values())
    meal = compose_meal([line, line])
    assert len(meal.lines) == 2
    assert meal.totals.quantities[NutrientKey.PROTEIN_G] == Decimal("138.4")


def test_empty_meal_rejected() -> None:
    with pytest.raises(EmptyMealError):
        compose_meal([])


def test_weakest_link_confidence() -> None:
    strong = _line(1, "1", "1 SERVG", _chicken_values())
    weak = _line(
        2, "1", "1 SERVG", {NutrientKey.CALORIES_KCAL: "100"}, confidence=Confidence.ESTIMATED
    )
    meal = compose_meal([strong, weak])
    assert meal.confidence is Confidence.ESTIMATED


def test_derived_meal_totals_carry_no_published_zero() -> None:
    line = _line(
        1,
        "2",
        "1 SERVG",
        {NutrientKey.TRANS_FAT_G: "0", NutrientKey.PROTEIN_G: "10"},
    )
    meal = compose_meal([line])
    assert meal.totals.published_zero == frozenset()
    assert meal.totals.quantities[NutrientKey.TRANS_FAT_G] == Decimal("0")
    assert presence_of(meal.totals, NutrientKey.TRANS_FAT_G) is NutrientPresence.KNOWN_VALUE


def test_meal_line_rejects_zero_servings() -> None:
    with pytest.raises(ValueError, match="servings must be positive"):
        _line(1, "0", "1 SERVG", _chicken_values())


def test_meal_line_rejects_negative_servings() -> None:
    with pytest.raises(ValueError, match="servings must be positive"):
        _line(1, "-2.5", "1 SERVG", _chicken_values())


def test_meal_line_accepts_positive_decimal_servings() -> None:
    line = _line(1, "0.5", "1 SERVG", _chicken_values())
    assert line.servings == Decimal("0.5")
