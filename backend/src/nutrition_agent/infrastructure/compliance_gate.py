"""Compliance gate: the single mandatory checkpoint before any network access.

ADR-010 records the owner's limited accepted-risk posture. Default mode remains
`blocked`; every authorized manual or scheduled path must opt in explicitly.
No code path may bypass this gate.
"""

from __future__ import annotations

from enum import StrEnum

from nutrition_agent.domain.stacks.ingestion import ErrorCode


class IngestionMode(StrEnum):
    BLOCKED = "blocked"
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class BlockedByPolicy(Exception):
    def __init__(self, detail: str = "ingestion blocked by compliance policy") -> None:
        super().__init__(detail)
        self.code = ErrorCode.TRANSPORT_BLOCKED_BY_POLICY


class ComplianceGate:
    def __init__(self, mode: IngestionMode) -> None:
        self._mode = mode

    @property
    def mode(self) -> IngestionMode:
        return self._mode

    def ensure_fetch_allowed(self, source_system: str) -> None:
        if self._mode not in {IngestionMode.MANUAL, IngestionMode.SCHEDULED}:
            raise BlockedByPolicy(
                f"STACKS_INGESTION_MODE={self._mode.value}; fetching '{source_system}' "
                "is disabled by the default-deny ADR-010 compliance policy"
            )
