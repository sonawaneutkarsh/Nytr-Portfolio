"""Authenticated application orchestration for M12A workout ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from nutrition_agent.application.ports import TrainingSessionRepository
from nutrition_agent.domain.training import TrainingBatchOutcome
from nutrition_agent.domain.training.validation import (
    TrainingBatchRejected,
    TrainingRejectedField,
    validate_training_batch,
)


class TrainingClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class TrainingSyncDeps:
    repository: TrainingSessionRepository
    clock: TrainingClock


@dataclass(frozen=True)
class TrainingRejected:
    errors: tuple[TrainingRejectedField, ...]


@dataclass(frozen=True)
class TrainingAccepted:
    user_id: UUID
    outcome: TrainingBatchOutcome


TrainingUseCaseResult = TrainingAccepted | TrainingRejected


class TrainingSessionSyncUseCase:
    """Validate then atomically apply one owner-scoped workout batch."""

    def __init__(self, deps: TrainingSyncDeps) -> None:
        self._deps = deps

    def submit(
        self,
        user_id: UUID,
        raw_batch_id: object,
        raw_added: object,
        raw_deleted: object,
    ) -> TrainingUseCaseResult:
        try:
            batch = validate_training_batch(
                raw_batch_id,
                raw_added if raw_added is not None else [],
                raw_deleted if raw_deleted is not None else [],
                now=self._deps.clock.now(),
            )
        except TrainingBatchRejected as exc:
            return TrainingRejected(exc.errors)
        return TrainingAccepted(user_id, self._deps.repository.apply_batch(user_id, batch))


__all__ = [
    "TrainingAccepted",
    "TrainingRejected",
    "TrainingSessionSyncUseCase",
    "TrainingSyncDeps",
]
