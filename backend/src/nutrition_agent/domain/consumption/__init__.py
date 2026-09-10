"""Immutable user-confirmed plan-consumption records (M7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ConsumptionState(StrEnum):
    EATEN = "eaten"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    ALTERNATIVE = "alternative"


@dataclass(frozen=True)
class ConsumptionEntry:
    entry_id: UUID
    user_id: UUID
    plan_run_id: UUID
    plan_version_id: UUID
    item_id: UUID
    state: ConsumptionState
    client_event_id: UUID
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")


@dataclass(frozen=True)
class RecordConsumptionOutcome:
    entry: ConsumptionEntry
    created: bool


__all__ = [
    "ConsumptionEntry",
    "ConsumptionState",
    "RecordConsumptionOutcome",
]
