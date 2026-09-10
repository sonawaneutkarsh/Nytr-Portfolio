"""Immutable factual consumption of one selected next-meal recommendation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from nutrition_agent.domain.next_meal import NextMealRecommendation, NextMealStatus
from nutrition_agent.domain.nutrition.ledger import NutritionAuthority
from nutrition_agent.domain.stacks.entities import NutrientKey


@dataclass(frozen=True)
class NextMealConsumptionEntry:
    entry_id: UUID
    user_id: UUID
    recommendation_id: UUID
    client_event_id: UUID
    local_date: date
    timezone: str
    recorded_at: datetime
    recommendation_artifact_sha256: str
    next_meal_policy_version: str
    meal_context: str
    menu_period: str
    candidate_id: str
    item_name: str
    serving_description: str
    configuration_summary: str | None
    nutrition_authority: NutritionAuthority
    nutrition_confidence: str | None
    calories_kcal: Decimal | None
    protein_g: Decimal | None
    unknown_nutrients: tuple[str, ...]
    selected_candidate_jsonb: dict[str, object]
    selected_candidate_sha256: str

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        for nutrition_value in (self.calories_kcal, self.protein_g):
            if nutrition_value is not None and (
                not isinstance(nutrition_value, Decimal)
                or not nutrition_value.is_finite()
                or nutrition_value < 0
            ):
                raise ValueError("nutrition values must be finite and nonnegative")
        for text_value in (
            self.timezone,
            self.next_meal_policy_version,
            self.meal_context,
            self.menu_period,
            self.candidate_id,
            self.item_name,
            self.serving_description,
        ):
            if not text_value.strip():
                raise ValueError("next-meal consumption text fields must be non-empty")
        for digest_value in (
            self.recommendation_artifact_sha256,
            self.selected_candidate_sha256,
        ):
            if len(digest_value) != 64 or any(
                character not in "0123456789abcdef" for character in digest_value
            ):
                raise ValueError("snapshot digests must be lowercase SHA-256")
        object.__setattr__(self, "unknown_nutrients", tuple(sorted(set(self.unknown_nutrients))))
        object.__setattr__(
            self,
            "selected_candidate_jsonb",
            _clone_document(self.selected_candidate_jsonb),
        )

    def request_facts(self) -> tuple[object, ...]:
        """Logical immutable request; excludes server-generated identity/time."""

        return (
            self.user_id,
            self.recommendation_id,
            self.client_event_id,
            self.local_date,
            self.timezone,
            self.recommendation_artifact_sha256,
            self.next_meal_policy_version,
            self.meal_context,
            self.menu_period,
            self.candidate_id,
            self.item_name,
            self.serving_description,
            self.configuration_summary,
            self.nutrition_authority,
            self.nutrition_confidence,
            self.calories_kcal,
            self.protein_g,
            self.unknown_nutrients,
            self.selected_candidate_jsonb,
            self.selected_candidate_sha256,
        )


@dataclass(frozen=True)
class RecordNextMealConsumptionOutcome:
    entry: NextMealConsumptionEntry
    created: bool


def _digest(value: dict[str, object]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _clone_document(value: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], json.loads(json.dumps(value)))


def _required_object(parent: dict[str, object], key: str) -> dict[str, object]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"recommendation {key} is unavailable")
    return value


def _required_text(parent: dict[str, object], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"recommendation {key} is unavailable")
    return value


def _nutrition_value(quantities: dict[str, object], key: NutrientKey) -> Decimal | None:
    value = quantities.get(key.value)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("recommendation nutrition must use decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("recommendation nutrition is invalid") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("recommendation nutrition must be finite and nonnegative")
    return result


def _candidate_identity(
    candidate: dict[str, object],
) -> tuple[str, str, str | None, NutritionAuthority]:
    estimate = candidate.get("configurable_estimate")
    if estimate is not None:
        if not isinstance(estimate, dict):
            raise ValueError("configurable recommendation is malformed")
        definition = _required_object(estimate, "definition")
        item_name = _required_text(definition, "display_name")
        summary = _required_text(definition, "configuration_summary")
        estimate_payload = _required_object(definition, "estimate")
        state = _required_text(estimate_payload, "state")
        if state == "complete_estimate":
            authority = NutritionAuthority.ESTIMATED
        elif state == "partial_estimate":
            authority = NutritionAuthority.PARTIAL
        else:
            raise ValueError("configurable recommendation nutrition state is unsupported")
        return item_name, summary, summary, authority

    lines = candidate.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("recommendation has no selected food lines")
    names: list[str] = []
    portions: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError("recommendation food line is malformed")
        name = _required_text(line, "name_normalized")
        servings = _required_text(line, "servings")
        names.append(name)
        portions.append(f"{servings} × {name}")
    return " + ".join(names), " + ".join(portions), None, NutritionAuthority.OFFICIAL


def snapshot_next_meal_consumption(
    *,
    recommendation: NextMealRecommendation,
    entry_id: UUID,
    client_event_id: UUID,
    recorded_at: datetime,
) -> NextMealConsumptionEntry:
    """Resolve one exact factual snapshot solely from the persisted artifact."""

    if recommendation.status is not NextMealStatus.RECOMMENDED:
        raise ValueError("only a successful next-meal recommendation can be consumed")
    if _digest(recommendation.artifact_jsonb) != recommendation.artifact_sha256:
        raise ValueError("recommendation artifact digest does not match")

    artifact = recommendation.artifact_jsonb
    candidate = _clone_document(_required_object(artifact, "selected"))
    opportunity = _required_object(artifact, "selected_opportunity")
    totals = _required_object(candidate, "totals")
    quantities = _required_object(totals, "quantities")
    candidate_id = _required_text(candidate, "candidate_id")
    policy_version = _required_text(artifact, "next_meal_policy_version")
    meal_context = _required_text(opportunity, "context")
    menu_period = _required_text(opportunity, "menu_period")
    item_name, serving_description, configuration_summary, authority = _candidate_identity(
        candidate
    )
    confidence_raw = totals.get("confidence")
    confidence = confidence_raw if isinstance(confidence_raw, str) else None
    if candidate.get("configurable_estimate") is None and confidence not in {
        "official_published",
        "official_component_sum",
    }:
        raise ValueError("strict recommendation nutrition authority is unsupported")
    unavailable = totals.get("declared_unavailable", [])
    if not isinstance(unavailable, list) or not all(
        isinstance(value, str) for value in unavailable
    ):
        raise ValueError("recommendation unavailable nutrients are malformed")
    unknown = set(unavailable)
    unknown.update(key.value for key in NutrientKey if key.value not in quantities)

    return NextMealConsumptionEntry(
        entry_id=entry_id,
        user_id=recommendation.user_id,
        recommendation_id=recommendation.recommendation_id,
        client_event_id=client_event_id,
        local_date=recommendation.local_date,
        timezone=recommendation.timezone,
        recorded_at=recorded_at,
        recommendation_artifact_sha256=recommendation.artifact_sha256,
        next_meal_policy_version=policy_version,
        meal_context=meal_context,
        menu_period=menu_period,
        candidate_id=candidate_id,
        item_name=item_name,
        serving_description=serving_description,
        configuration_summary=configuration_summary,
        nutrition_authority=authority,
        nutrition_confidence=confidence,
        calories_kcal=_nutrition_value(quantities, NutrientKey.CALORIES_KCAL),
        protein_g=_nutrition_value(quantities, NutrientKey.PROTEIN_G),
        unknown_nutrients=tuple(unknown),
        selected_candidate_jsonb=candidate,
        selected_candidate_sha256=_digest(candidate),
    )


__all__ = [
    "NextMealConsumptionEntry",
    "RecordNextMealConsumptionOutcome",
    "snapshot_next_meal_consumption",
]
