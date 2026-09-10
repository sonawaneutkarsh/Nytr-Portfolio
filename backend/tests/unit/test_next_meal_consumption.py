from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.next_meal_consumption import (
    NextMealConsumptionNotFound,
    RecordNextMealConsumptionUseCase,
)
from nutrition_agent.application.ports import DuplicateNextMealConsumptionError
from nutrition_agent.db.in_memory_repos import (
    InMemoryNextMealConsumptionRepository,
    InMemoryNextMealRecommendationRepository,
)
from nutrition_agent.domain.next_meal import (
    NextMealRecommendation,
    NextMealStatus,
    recommendation_from_artifact,
)
from nutrition_agent.domain.next_meal_consumption import snapshot_next_meal_consumption
from nutrition_agent.domain.nutrition.ledger import NutritionAuthority

OWNER = UUID(int=1601)
OTHER = UUID(int=1602)
DAY = date(2026, 9, 5)
NOW = datetime(2026, 9, 5, 16, tzinfo=UTC)
RECOMMENDATION_ID = UUID(int=1)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


class Ids:
    def __init__(self) -> None:
        self.value = 1700

    def new_id(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def _candidate(*, estimated: bool = False, calories: str | None = "640") -> dict[str, object]:
    quantities: dict[str, str] = {"protein_g": "44.5"}
    if calories is not None:
        quantities["calories_kcal"] = calories
    candidate: dict[str, object] = {
        "candidate_id": "lunch-01",
        "lines": [
            {
                "name_normalized": "grilled chicken",
                "servings": "1",
                "food_id": str(UUID(int=40)),
                "offering_id": str(UUID(int=41)),
                "profile_content_sha256": "a" * 64,
                "source_mid": "123",
            }
        ],
        "totals": {
            "quantities": quantities,
            "declared_unavailable": ["vitamin_d_mcg"],
            "confidence": "official_published",
        },
        "provenance": {
            "food_ids": [str(UUID(int=40))],
            "offering_ids": [str(UUID(int=41))],
            "profile_content_sha256s": ["a" * 64],
        },
    }
    if estimated:
        candidate["lines"] = []
        candidate["configurable_estimate"] = {
            "definition": {
                "display_name": "CYO Halal Bowl",
                "configuration_summary": "rice + chicken + eggs",
                "estimate": {
                    "state": "partial_estimate",
                    "unknown_nutrients": ["sodium_mg"],
                },
            }
        }
        candidate["totals"] = {
            "quantities": quantities,
            "declared_unavailable": ["sodium_mg"],
            "confidence": "estimated",
        }
    return candidate


def _recommendation(
    *,
    recommendation_id: UUID = RECOMMENDATION_ID,
    owner: UUID = OWNER,
    candidate: dict[str, object] | None = None,
    status: NextMealStatus = NextMealStatus.RECOMMENDED,
) -> NextMealRecommendation:
    artifact: dict[str, object] = {
        "artifact_kind": "next_meal_recommendation",
        "artifact_version": "m16a.v1",
        "decision_at": NOW.isoformat(),
        "local_date": DAY.isoformat(),
        "timezone": "America/New_York",
        "next_meal_policy_version": "next-meal.remaining-opportunities.v1",
        "status": status.value,
        "reason_codes": [],
    }
    if status is NextMealStatus.RECOMMENDED:
        artifact["selected_opportunity"] = {
            "context": "post_workout_lunch",
            "menu_period": "Lunch",
            "window": ["12:30:00", "13:15:00"],
        }
        artifact["selected"] = candidate or _candidate()
    return recommendation_from_artifact(
        recommendation_id=recommendation_id,
        user_id=owner,
        client_request_id=UUID(int=recommendation_id.int + 100),
        local_date=DAY,
        timezone="America/New_York",
        decision_at=NOW,
        target_policy_version_id=UUID(int=90),
        status=status,
        reason_codes=(),
        artifact=artifact,
    )


def test_snapshot_freezes_exact_selected_candidate_and_official_nutrition() -> None:
    recommendation = _recommendation()
    entry = snapshot_next_meal_consumption(
        recommendation=recommendation,
        entry_id=UUID(int=2),
        client_event_id=UUID(int=3),
        recorded_at=NOW,
    )

    assert entry.recommendation_id == UUID(int=1)
    assert entry.meal_context == "post_workout_lunch"
    assert entry.menu_period == "Lunch"
    assert entry.item_name == "grilled chicken"
    assert entry.serving_description == "1 × grilled chicken"
    assert entry.nutrition_authority is NutritionAuthority.OFFICIAL
    assert entry.calories_kcal == Decimal("640")
    assert entry.protein_g == Decimal("44.5")
    assert "vitamin_d_mcg" in entry.unknown_nutrients
    assert len(entry.selected_candidate_sha256) == 64

    original_snapshot = deepcopy(entry.selected_candidate_jsonb)
    recommendation.artifact_jsonb["selected"] = _candidate(calories="999")
    assert entry.selected_candidate_jsonb == original_snapshot
    assert entry.calories_kcal == Decimal("640")


def test_nullable_estimated_nutrition_remains_partial_and_unknown() -> None:
    entry = snapshot_next_meal_consumption(
        recommendation=_recommendation(candidate=_candidate(estimated=True, calories=None)),
        entry_id=UUID(int=4),
        client_event_id=UUID(int=5),
        recorded_at=NOW,
    )
    assert entry.item_name == "CYO Halal Bowl"
    assert entry.configuration_summary == "rice + chicken + eggs"
    assert entry.nutrition_authority is NutritionAuthority.PARTIAL
    assert entry.calories_kcal is None
    assert entry.protein_g == Decimal("44.5")
    assert "calories_kcal" in entry.unknown_nutrients
    assert "sodium_mg" in entry.unknown_nutrients


def test_only_successful_recommendations_are_consumable() -> None:
    with pytest.raises(ValueError, match="successful"):
        snapshot_next_meal_consumption(
            recommendation=_recommendation(status=NextMealStatus.NO_APPROVED_PROTEIN_TARGET),
            entry_id=UUID(int=6),
            client_event_id=UUID(int=7),
            recorded_at=NOW,
        )


def test_explicit_use_case_creates_once_replays_and_rejects_conflicts() -> None:
    recommendations = InMemoryNextMealRecommendationRepository()
    recommendation = _recommendation()
    recommendations.save(recommendation)
    consumptions = InMemoryNextMealConsumptionRepository()
    clock = Clock()
    use_case = RecordNextMealConsumptionUseCase(
        recommendations=recommendations,
        consumptions=consumptions,
        clock=clock,
        ids=Ids(),
    )
    kwargs = {
        "user_id": OWNER,
        "recommendation_id": UUID(int=1),
        "client_event_id": UUID(int=8),
    }

    first = use_case.execute(**kwargs)
    clock.value = datetime(2026, 9, 6, tzinfo=UTC)
    replay = use_case.execute(**kwargs)
    assert first.created is True
    assert replay.created is False
    assert replay.entry == first.entry
    assert len(consumptions.entries) == 1

    with pytest.raises(DuplicateNextMealConsumptionError):
        use_case.execute(**{**kwargs, "client_event_id": UUID(int=9)})
    with pytest.raises(NextMealConsumptionNotFound):
        use_case.execute(**{**kwargs, "user_id": OTHER})
    assert len(consumptions.entries) == 1
