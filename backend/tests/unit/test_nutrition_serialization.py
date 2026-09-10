"""Deterministic serialization: golden byte-stability + pinned replay."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.meal import MealLine, compose_meal
from nutrition_agent.domain.nutrition.serialization import (
    daily_to_json_bytes,
    facts_to_dict,
    facts_to_json_bytes,
)
from nutrition_agent.domain.nutrition.totals import sum_daily
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    NutrientKey,
    ServingBasisKind,
)

GOLDEN_FACTS_JSON = (
    b'{"confidence":"official_published",'
    b'"declared_unavailable":["fiber_g"],'
    b'"presences":{"calories_kcal":"known_value","fiber_g":"declared_unavailable",'
    b'"protein_g":"known_value","sodium_mg":"published_zero"},'
    b'"published_zero":["sodium_mg"],'
    b'"quantities":{"calories_kcal":"500","protein_g":"30.5","sodium_mg":"0"}}'
)  # unknown_absent keys are omitted from presences by design


def _facts() -> NutritionFacts:
    return NutritionFacts(
        quantities={
            NutrientKey.CALORIES_KCAL: Decimal(500),
            NutrientKey.PROTEIN_G: Decimal("30.5"),
            NutrientKey.SODIUM_MG: Decimal(0),
        },
        published_zero=frozenset({NutrientKey.SODIUM_MG}),
        declared_unavailable=frozenset({NutrientKey.FIBER_G}),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )


def test_golden_facts_bytes_stable() -> None:
    assert facts_to_json_bytes(_facts()) == GOLDEN_FACTS_JSON


def test_facts_dict_sorted_and_stringified() -> None:
    payload = facts_to_dict(_facts())
    assert payload["quantities"]["protein_g"] == "30.5"
    assert payload["published_zero"] == ["sodium_mg"]
    assert payload["declared_unavailable"] == ["fiber_g"]
    assert payload["presences"]["sodium_mg"] == "published_zero"
    assert "iron_mg" not in payload["presences"]  # unknown_absent keys are omitted


def test_none_facts_serializes_explicitly() -> None:
    assert facts_to_json_bytes(None) == b'{"totals":null}'


def test_full_pipeline_replay_byte_identical() -> None:
    def build() -> bytes:
        line = MealLine(
            food_id=UUID(int=7),
            profile_content_sha256="abc123",
            parser_version="2026-08-21.m2.1",
            servings=Decimal("2"),
            serving_basis_raw="1 SERVG",
            serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
            facts=_facts(),
        )
        lunch = compose_meal([line])
        dinner = compose_meal([line])
        return daily_to_json_bytes(sum_daily([lunch, dinner]))

    assert build() == build()
    assert b'"engine_policy_version":"2026-08-21.m3.1"' in build()
    # derived totals in serialized form carry no published_zero
    assert b'"published_zero":[]' in build()
