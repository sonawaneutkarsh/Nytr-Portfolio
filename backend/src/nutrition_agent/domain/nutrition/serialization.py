"""Deterministic serialization of engine artifacts.

Stringified Decimals, sorted keys, enum values as strings; byte-stable across
runs and processes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nutrition_agent.domain.nutrition.facts import ALL_NUTRIENT_KEYS, NutritionFacts, presence_of
from nutrition_agent.domain.nutrition.meal import ComposedMeal
from nutrition_agent.domain.nutrition.totals import DailyTotals
from nutrition_agent.domain.stacks.entities import NutrientKey

_KEY_ORDER: list[NutrientKey] = sorted(ALL_NUTRIENT_KEYS, key=lambda key: key.value)


def facts_to_dict(facts: NutritionFacts | None) -> dict[str, Any]:
    if facts is None:
        return {"totals": None}
    quantities: dict[str, str] = {
        key.value: str(facts.quantities[key]) for key in _KEY_ORDER if key in facts.quantities
    }
    presences: dict[str, str] = {
        key.value: presence_of(facts, key).value
        for key in _KEY_ORDER
        if presence_of(facts, key) is not None and presence_of(facts, key).value != "unknown_absent"
    }
    return {
        "confidence": facts.confidence.value,
        "declared_unavailable": sorted(key.value for key in facts.declared_unavailable),
        "presences": presences,
        "published_zero": sorted(key.value for key in facts.published_zero),
        "quantities": quantities,
    }


def meal_to_dict(meal: ComposedMeal) -> dict[str, Any]:
    return {
        "confidence": meal.confidence.value,
        "engine_policy_version": meal.engine_policy_version,
        "lines": [
            {
                "food_id": str(line.food_id),
                "parser_version": line.parser_version,
                "profile_content_sha256": line.profile_content_sha256,
                "serving_basis_kind": line.serving_basis_kind.value,
                "serving_basis_raw": line.serving_basis_raw,
                "servings": str(line.servings),
            }
            for line in meal.lines
        ],
        "totals": facts_to_dict(meal.totals),
    }


def daily_to_dict(daily: DailyTotals) -> dict[str, Any]:
    return {
        "engine_policy_version": daily.engine_policy_version,
        "meals": [meal_to_dict(meal) for meal in daily.meals],
        "totals": facts_to_dict(daily.totals),
    }


def to_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def facts_to_json_bytes(facts: NutritionFacts | None) -> bytes:
    return to_json_bytes(facts_to_dict(facts))


def daily_to_json_bytes(daily: DailyTotals) -> bytes:
    return to_json_bytes(daily_to_dict(daily))
