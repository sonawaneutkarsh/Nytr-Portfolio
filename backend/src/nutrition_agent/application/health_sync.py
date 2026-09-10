"""Health body-mass sync use case (M5, ADR-016).

Thin orchestration: validate the raw payload into a domain SyncBatch
(all-or-nothing), durably apply it through the repository, and surface
observability counts. No arithmetic, no derived data, no HealthKit concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from nutrition_agent.application.ports import HealthBodyMassRepository
from nutrition_agent.domain.health.entities import BatchOutcome, HealthSyncStatus
from nutrition_agent.domain.health.validation import (
    BatchRejected,
    RejectedField,
    validate_body_mass_batch,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class HealthSyncDeps:
    """Dependency container for :class:`HealthBodyMassSyncUseCase`."""

    def __init__(self, repository: HealthBodyMassRepository, clock: Clock) -> None:
        self.repository = repository
        self.clock = clock


@dataclass(frozen=True)
class Rejected:
    """Domain-level rejection; maps to HTTP 400 at the API boundary."""

    errors: tuple[RejectedField, ...]


@dataclass(frozen=True)
class Accepted:
    """Batch durably applied; maps to HTTP 200 at the API boundary."""

    user_id: UUID
    outcome: BatchOutcome


UseCaseResult = Accepted | Rejected


class HealthBodyMassSyncUseCase:
    """Validates and durably ingests one authenticated sync batch."""

    def __init__(self, deps: HealthSyncDeps) -> None:
        self._deps = deps

    def submit(
        self,
        user_id: UUID,
        raw_batch_id: object,
        raw_added: object,
        raw_deleted: object,
    ) -> UseCaseResult:
        # Absent lists are legal (an empty batch is a no-op); explicit
        # non-list values still fail validation below.
        try:
            batch = validate_body_mass_batch(
                raw_batch_id,
                raw_added if raw_added is not None else [],
                raw_deleted if raw_deleted is not None else [],
                now=self._deps.clock.now(),
            )
        except BatchRejected as exc:
            return Rejected(errors=exc.errors)
        outcome = self._deps.repository.apply_batch(user_id, batch)
        return Accepted(user_id=user_id, outcome=outcome)

    def status(self, user_id: UUID) -> HealthSyncStatus:
        return self._deps.repository.status_summary(user_id)
