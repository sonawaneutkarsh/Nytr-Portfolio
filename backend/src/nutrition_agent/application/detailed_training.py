"""Use cases for importing and reading immutable detailed training revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from nutrition_agent.application.ports import (
    Clock,
    DetailedTrainingRepository,
    DetailedTrainingSource,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportOutcome,
    DetailedTrainingSessionSummary,
    StoredDetailedTrainingSession,
)


class IncompleteDetailedTrainingImportError(ValueError):
    """The source could not prove that its bounded page sequence was complete."""


@dataclass(frozen=True)
class ImportDetailedTrainingSessionsUseCase:
    source: DetailedTrainingSource
    repository: DetailedTrainingRepository
    clock: Clock

    def execute(
        self, *, user_id: UUID, since: datetime | None = None
    ) -> DetailedTrainingImportOutcome:
        batch = self.source.load_changes(since)
        if not batch.complete:
            raise IncompleteDetailedTrainingImportError(
                "detailed training source result is incomplete"
            )
        return self.repository.apply_import(user_id, batch, self.clock.now())


@dataclass(frozen=True)
class ListDetailedTrainingSessionsUseCase:
    repository: DetailedTrainingRepository

    def execute(
        self, *, user_id: UUID, limit: int = 50
    ) -> tuple[DetailedTrainingSessionSummary, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        return self.repository.list_latest(user_id, limit)


@dataclass(frozen=True)
class GetDetailedTrainingSessionUseCase:
    repository: DetailedTrainingRepository

    def execute(self, *, user_id: UUID, revision_id: UUID) -> StoredDetailedTrainingSession | None:
        return self.repository.get_by_revision_id(user_id, revision_id)


__all__ = [
    "GetDetailedTrainingSessionUseCase",
    "ImportDetailedTrainingSessionsUseCase",
    "IncompleteDetailedTrainingImportError",
    "ListDetailedTrainingSessionsUseCase",
]
