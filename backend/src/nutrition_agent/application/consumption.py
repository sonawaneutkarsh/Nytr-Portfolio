"""Application orchestration for immutable plan-consumption events (M7)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from nutrition_agent.application.ports import (
    Clock,
    ConsumptionRepository,
    IdGenerator,
)
from nutrition_agent.domain.consumption import (
    ConsumptionEntry,
    ConsumptionState,
    RecordConsumptionOutcome,
)


def _require_uuid(name: str, value: object) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID")


class RecordConsumptionUseCase:
    def __init__(
        self,
        repository: ConsumptionRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        plan_run_id: UUID,
        plan_version_id: UUID,
        item_id: UUID,
        state: ConsumptionState,
        client_event_id: UUID,
    ) -> RecordConsumptionOutcome:
        for name, value in (
            ("user_id", user_id),
            ("plan_run_id", plan_run_id),
            ("plan_version_id", plan_version_id),
            ("item_id", item_id),
            ("client_event_id", client_event_id),
        ):
            _require_uuid(name, value)
        if not isinstance(state, ConsumptionState):
            raise ValueError("state must be a ConsumptionState")

        entry_id = self._ids.new_id()
        _require_uuid("generated entry_id", entry_id)
        recorded_at = self._clock.now()
        if not isinstance(recorded_at, datetime):
            raise ValueError("clock must return a datetime")
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")

        entry = ConsumptionEntry(
            entry_id=entry_id,
            user_id=user_id,
            plan_run_id=plan_run_id,
            plan_version_id=plan_version_id,
            item_id=item_id,
            state=state,
            client_event_id=client_event_id,
            recorded_at=recorded_at,
        )
        return self._repository.save(entry)


class ListConsumptionForRunUseCase:
    def __init__(self, repository: ConsumptionRepository) -> None:
        self._repository = repository

    def execute(
        self,
        *,
        user_id: UUID,
        plan_run_id: UUID,
    ) -> tuple[ConsumptionEntry, ...]:
        _require_uuid("user_id", user_id)
        _require_uuid("plan_run_id", plan_run_id)
        return self._repository.list_for_run(user_id, plan_run_id)


__all__ = [
    "ListConsumptionForRunUseCase",
    "RecordConsumptionUseCase",
]
