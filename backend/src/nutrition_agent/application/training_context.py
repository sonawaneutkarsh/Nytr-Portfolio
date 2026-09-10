"""Owner-scoped read orchestration for deterministic training-day context."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from nutrition_agent.application.ports import TrainingSessionRepository
from nutrition_agent.domain.training import local_day_utc_bounds
from nutrition_agent.domain.training.context import (
    OWNER_STRENGTH_TRAINING_DAY_POLICY_V1,
    TrainingDayContext,
    TrainingDayPolicy,
    evaluate_training_day_context,
)


class TrainingDayContextUseCase:
    """Read one owner's observations and apply a pinned domain policy."""

    def __init__(
        self,
        *,
        training: TrainingSessionRepository,
        policy: TrainingDayPolicy = OWNER_STRENGTH_TRAINING_DAY_POLICY_V1,
    ) -> None:
        self._training = training
        self._policy = policy

    def execute(
        self,
        *,
        user_id: UUID,
        local_date: date,
        timezone: str,
    ) -> TrainingDayContext:
        start, end = local_day_utc_bounds(local_date, timezone)
        return evaluate_training_day_context(
            local_date=local_date,
            timezone=timezone,
            training_sessions=self._training.list_active(user_id, start, end),
            policy=self._policy,
        )


__all__ = ["TrainingDayContextUseCase"]
