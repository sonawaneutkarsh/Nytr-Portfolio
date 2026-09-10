"""Explicit application workflow for factual next-meal consumption."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from nutrition_agent.application.ports import (
    Clock,
    IdGenerator,
    NextMealConsumptionRepository,
    NextMealRecommendationRepository,
)
from nutrition_agent.domain.next_meal_consumption import (
    NextMealConsumptionEntry,
    RecordNextMealConsumptionOutcome,
    snapshot_next_meal_consumption,
)


class NextMealConsumptionUnavailable(LookupError):
    """The owned recommendation exists but has no selected meal to consume."""


class NextMealConsumptionNotFound(LookupError):
    """No recommendation owned by the caller has the requested identity."""


class RecordNextMealConsumptionUseCase:
    def __init__(
        self,
        *,
        recommendations: NextMealRecommendationRepository,
        consumptions: NextMealConsumptionRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._recommendations = recommendations
        self._consumptions = consumptions
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        recommendation_id: UUID,
        client_event_id: UUID,
    ) -> RecordNextMealConsumptionOutcome:
        recommendation = self._recommendations.find_by_id(user_id, recommendation_id)
        if recommendation is None:
            raise NextMealConsumptionNotFound("next-meal recommendation not found")
        now = self._clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        try:
            entry = snapshot_next_meal_consumption(
                recommendation=recommendation,
                entry_id=self._ids.new_id(),
                client_event_id=client_event_id,
                recorded_at=now,
            )
        except ValueError as exc:
            raise NextMealConsumptionUnavailable(str(exc)) from exc
        return self._consumptions.save(entry)


class GetNextMealConsumptionUseCase:
    def __init__(self, repository: NextMealConsumptionRepository) -> None:
        self._repository = repository

    def execute(self, *, user_id: UUID, recommendation_id: UUID) -> NextMealConsumptionEntry | None:
        """Return the owned event when this recommendation has been consumed."""
        return self._repository.find_for_recommendation(user_id, recommendation_id)


__all__ = [
    "GetNextMealConsumptionUseCase",
    "NextMealConsumptionNotFound",
    "NextMealConsumptionUnavailable",
    "RecordNextMealConsumptionUseCase",
]
