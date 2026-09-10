"""Body-mass synchronization entities (M5, ADR-016).

Identity is (user_id, hk_sample_uuid): HKObject.uuid is HealthKit-assigned and
stable, and HKDeletedObject.uuid carries the same UUID, so retries/crashes/
duplicates collapse onto one logical record. Values are canonical kilograms as
exact Decimals transported as decimal strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

METRIC_BODY_MASS = "body_mass"


@dataclass(frozen=True)
class BodyMassSample:
    """One active HealthKit body-mass measurement, normalized to kilograms."""

    sample_uuid: UUID
    value_kg: Decimal
    sample_start: datetime
    sample_end: datetime
    source_name: str | None = None
    source_bundle_id: str | None = None


@dataclass(frozen=True)
class SampleDeletion:
    """A HealthKit deletion notice; ingestion records a tombstone for it."""

    sample_uuid: UUID


@dataclass(frozen=True)
class StoredSample:
    """A durable row: either an active measurement or a tombstone.

    ``value_kg``/``sample_start``/``sample_end`` are NULL exactly for
    tombstone-only rows (deletion received before its sample). CHECK
    constraints in migration 0002 enforce the same shape in Postgres.
    """

    user_id: UUID
    sample_uuid: UUID
    value_kg: Decimal | None
    sample_start: datetime | None
    sample_end: datetime | None
    source_name: str | None
    source_bundle_id: str | None
    ingested_at: datetime
    tombstoned_at: datetime | None


@dataclass(frozen=True)
class SyncBatch:
    """One idempotent upload batch (adds + deletions, all-or-nothing)."""

    client_batch_id: UUID
    added: tuple[BodyMassSample, ...]
    deletions: tuple[SampleDeletion, ...]


@dataclass(frozen=True)
class BatchOutcome:
    """Result of durably applying a batch (observability counts only)."""

    accepted_added: int
    duplicate_added: int
    applied_deletions: int
    duplicate_deletions: int


@dataclass(frozen=True)
class LatestSample:
    """Newest active sample summary used by status endpoints."""

    sample_uuid: UUID
    sample_start: datetime
    value_kg: Decimal


@dataclass(frozen=True)
class HealthSyncStatus:
    """Aggregate view for the sync-status endpoint (no per-row payloads)."""

    record_count: int
    tombstone_count: int
    latest_sample: LatestSample | None
    last_ingested_at: datetime | None
