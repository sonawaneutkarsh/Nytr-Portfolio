"""Application orchestration for the deterministic M9 body-mass trend."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from nutrition_agent.application.ports import BodyMassHistoryRepository
from nutrition_agent.domain.health.trend import (
    BodyMassTrendSummary,
    analysis_window_utc_bounds,
    calculate_body_mass_trend,
)


class BodyMassTrendUseCase:
    """Read one owner's active history and return the pure derived summary."""

    def __init__(self, repository: BodyMassHistoryRepository) -> None:
        self._repository = repository

    def execute(
        self,
        *,
        user_id: UUID,
        as_of_date: date,
        timezone: str,
    ) -> BodyMassTrendSummary:
        start, end = analysis_window_utc_bounds(as_of_date, timezone)
        observations = self._repository.list_active(user_id, start, end)
        return calculate_body_mass_trend(
            user_id=user_id,
            observations=observations,
            as_of_date=as_of_date,
            timezone=timezone,
        )
