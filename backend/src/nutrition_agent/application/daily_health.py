"""Pure-read orchestration for the M12A daily health summary."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from nutrition_agent.application.ports import (
    BodyMassHistoryRepository,
    TrainingSessionRepository,
)
from nutrition_agent.domain.training import (
    DailyHealthSummary,
    local_day_utc_bounds,
    summarize_daily_health,
)


class DailyHealthSummaryUseCase:
    """Read owner-scoped observations and delegate all derivation to domain code."""

    def __init__(
        self,
        *,
        body_mass: BodyMassHistoryRepository,
        training: TrainingSessionRepository,
    ) -> None:
        self._body_mass = body_mass
        self._training = training

    def execute(
        self,
        *,
        user_id: UUID,
        local_date: date,
        timezone: str,
    ) -> DailyHealthSummary:
        start, end = local_day_utc_bounds(local_date, timezone)
        return summarize_daily_health(
            local_date=local_date,
            timezone=timezone,
            body_mass_observations=self._body_mass.list_active(user_id, start, end),
            training_sessions=self._training.list_active(user_id, start, end),
        )


__all__ = ["DailyHealthSummaryUseCase"]
