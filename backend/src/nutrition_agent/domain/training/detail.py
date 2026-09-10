"""Immutable, source-identified detailed strength-training observations.

Detailed observations are additive to the high-level HealthKit workout ledger.
They do not establish that a training day occurred and they never alter
nutrition targets or planner inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class TrainingSetType(StrEnum):
    NORMAL = "normal"
    WARMUP = "warmup"
    DROPSET = "dropset"
    FAILURE = "failure"


class DetailedTrainingSourceSystem(StrEnum):
    """Sources permitted to supply structure, never high-level day authority."""

    HEVY = "hevy"


class DetailedTrainingSyncMode(StrEnum):
    BOOTSTRAP = "bootstrap"
    INCREMENTAL = "incremental"


class DetailedTrainingSyncStatus(StrEnum):
    SYNCED = "synced"
    INCOMPLETE = "incomplete"


class TrainingLoadUnit(StrEnum):
    KILOGRAM = "kg"
    POUND = "lb"


class TrainingDistanceUnit(StrEnum):
    METER = "m"


@dataclass(frozen=True)
class TrainingLoad:
    """Source load with its original unit and exact deterministic conversion."""

    value: Decimal
    unit: TrainingLoadUnit

    def __post_init__(self) -> None:
        _require_finite_decimal(self.value, "load value")

    @property
    def kilograms(self) -> Decimal:
        if self.unit is TrainingLoadUnit.KILOGRAM:
            return self.value
        return self.value * Decimal("0.45359237")


@dataclass(frozen=True)
class TrainingDistance:
    value: Decimal
    unit: TrainingDistanceUnit

    def __post_init__(self) -> None:
        _require_nonnegative_decimal(self.value, "distance value")


@dataclass(frozen=True)
class DetailedTrainingSet:
    """One ordered source set; missing quantitative fields remain unknown."""

    set_identity: str
    set_index: int
    set_type: TrainingSetType
    source_set_id: str | None = None
    reps: int | None = None
    load: TrainingLoad | None = None
    distance: TrainingDistance | None = None
    duration_seconds: Decimal | None = None
    rpe: Decimal | None = None
    custom_metric: Decimal | None = None

    def __post_init__(self) -> None:
        _require_text(self.set_identity, "set_identity")
        _require_nonnegative_int(self.set_index, "set_index")
        if self.source_set_id is not None:
            _require_text(self.source_set_id, "source_set_id")
        if self.reps is not None:
            _require_nonnegative_int(self.reps, "reps")
        if self.duration_seconds is not None:
            _require_nonnegative_decimal(self.duration_seconds, "duration_seconds")
        if self.rpe is not None:
            _require_nonnegative_decimal(self.rpe, "rpe")
        if self.custom_metric is not None:
            _require_nonnegative_decimal(self.custom_metric, "custom_metric")


@dataclass(frozen=True)
class ExercisePerformance:
    """One source-ordered exercise occurrence; names are not identity."""

    occurrence_identity: str
    source_exercise_id: str
    display_name: str
    exercise_order: int
    sets: tuple[DetailedTrainingSet, ...]
    notes: str | None = None
    superset_id: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.occurrence_identity, "occurrence_identity")
        _require_text(self.source_exercise_id, "source_exercise_id")
        _require_text(self.display_name, "display_name")
        _require_nonnegative_int(self.exercise_order, "exercise_order")
        if self.superset_id is not None:
            _require_nonnegative_int(self.superset_id, "superset_id")
        if tuple(item.set_index for item in self.sets) != tuple(
            sorted(item.set_index for item in self.sets)
        ):
            raise ValueError("sets must be ordered by set_index")
        identities = tuple(item.set_identity for item in self.sets)
        if len(identities) != len(set(identities)):
            raise ValueError("set identities must be unique within an exercise occurrence")


@dataclass(frozen=True)
class DetailedTrainingSession:
    """One immutable source revision of a detailed workout session."""

    source_system: DetailedTrainingSourceSystem
    source_session_id: str
    source_revision: str
    title: str
    started_at: datetime
    ended_at: datetime
    exercises: tuple[ExercisePerformance, ...]
    parser_version: str
    source_payload_sha256: str
    description: str | None = None
    routine_id: str | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.source_session_id, "source_session_id")
        _require_text(self.source_revision, "source_revision")
        _require_text(self.title, "title")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at precedes started_at")
        _require_text(self.parser_version, "parser_version")
        if len(self.source_payload_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_payload_sha256
        ):
            raise ValueError("source_payload_sha256 must be lowercase hexadecimal sha256")
        if self.routine_id is not None:
            _require_text(self.routine_id, "routine_id")
        if self.source_created_at is not None:
            _require_aware(self.source_created_at, "source_created_at")
        if self.source_updated_at is not None:
            _require_aware(self.source_updated_at, "source_updated_at")
        if tuple(item.exercise_order for item in self.exercises) != tuple(
            sorted(item.exercise_order for item in self.exercises)
        ):
            raise ValueError("exercises must be ordered by exercise_order")
        occurrences = tuple(item.occurrence_identity for item in self.exercises)
        if len(occurrences) != len(set(occurrences)):
            raise ValueError("exercise occurrence identities must be unique within a revision")


@dataclass(frozen=True)
class DetailedTrainingDeletion:
    source_system: DetailedTrainingSourceSystem
    source_session_id: str
    removed_at: datetime
    parser_version: str
    source_payload_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.source_session_id, "source_session_id")
        _require_aware(self.removed_at, "removed_at")
        _require_text(self.parser_version, "parser_version")
        if len(self.source_payload_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_payload_sha256
        ):
            raise ValueError("source_payload_sha256 must be lowercase hexadecimal sha256")


@dataclass(frozen=True)
class DetailedTrainingImportBatch:
    sessions: tuple[DetailedTrainingSession, ...]
    deletions: tuple[DetailedTrainingDeletion, ...]
    complete: bool

    def __post_init__(self) -> None:
        revision_keys = tuple(
            (item.source_system, item.source_session_id, item.source_revision)
            for item in self.sessions
        )
        if len(revision_keys) != len(set(revision_keys)):
            raise ValueError("duplicate detailed session revision in one import")
        deletion_keys = tuple(
            (item.source_system, item.source_session_id) for item in self.deletions
        )
        if len(deletion_keys) != len(set(deletion_keys)):
            raise ValueError("duplicate detailed session deletion in one import")


@dataclass(frozen=True)
class DetailedTrainingSourceFetch:
    """One bounded, fully observed source page sequence plus transport facts."""

    batch: DetailedTrainingImportBatch
    pages_fetched: int
    has_more: bool
    source_event_watermark: datetime
    logical_requests: int
    attempts_made: int
    retries: int
    parser_version: str
    provider_version: str

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.pages_fetched, "pages_fetched")
        _require_nonnegative_int(self.logical_requests, "logical_requests")
        _require_nonnegative_int(self.attempts_made, "attempts_made")
        _require_nonnegative_int(self.retries, "retries")
        _require_aware(self.source_event_watermark, "source_event_watermark")
        _require_text(self.parser_version, "parser_version")
        _require_text(self.provider_version, "provider_version")
        if self.pages_fetched < 1:
            raise ValueError("a source fetch must contain at least one page")
        if self.logical_requests < self.pages_fetched:
            raise ValueError("logical requests cannot be fewer than fetched pages")
        if self.attempts_made != self.logical_requests + self.retries:
            raise ValueError("attempts must equal logical requests plus retries")
        session_parser_mismatch = any(
            item.parser_version != self.parser_version for item in self.batch.sessions
        )
        deletion_parser_mismatch = any(
            item.parser_version != self.parser_version for item in self.batch.deletions
        )
        if session_parser_mismatch or deletion_parser_mismatch:
            raise ValueError("source records must match the fetch parser version")
        session_after_watermark = any(
            item.source_updated_at is not None
            and item.source_updated_at > self.source_event_watermark
            for item in self.batch.sessions
        )
        deletion_after_watermark = any(
            item.removed_at > self.source_event_watermark for item in self.batch.deletions
        )
        if session_after_watermark or deletion_after_watermark:
            raise ValueError("source event watermark cannot precede an observed event")
        if self.has_more == self.batch.complete:
            raise ValueError("complete source batches and has_more must be opposites")


@dataclass(frozen=True)
class DetailedTrainingSyncCheckpoint:
    """Immutable successful-source checkpoint; never contains credentials."""

    checkpoint_id: UUID
    user_id: UUID
    source_system: DetailedTrainingSourceSystem
    sync_mode: DetailedTrainingSyncMode
    bootstrap_completed: bool
    source_event_watermark: datetime
    parser_version: str
    provider_version: str
    pages_fetched: int
    logical_requests: int
    attempts_made: int
    retries: int
    completed_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.source_event_watermark, "source_event_watermark")
        _require_aware(self.completed_at, "completed_at")
        _require_text(self.parser_version, "parser_version")
        _require_text(self.provider_version, "provider_version")
        _require_nonnegative_int(self.pages_fetched, "pages_fetched")
        _require_nonnegative_int(self.logical_requests, "logical_requests")
        _require_nonnegative_int(self.attempts_made, "attempts_made")
        _require_nonnegative_int(self.retries, "retries")
        if not self.bootstrap_completed:
            raise ValueError("a successful sync checkpoint must complete bootstrap")
        if self.pages_fetched < 1:
            raise ValueError("a sync checkpoint must describe at least one page")
        if self.logical_requests < self.pages_fetched:
            raise ValueError("logical requests cannot be fewer than fetched pages")
        if self.attempts_made != self.logical_requests + self.retries:
            raise ValueError("attempts must equal logical requests plus retries")


@dataclass(frozen=True)
class DetailedTrainingSyncOutcome:
    status: DetailedTrainingSyncStatus
    mode: DetailedTrainingSyncMode
    sessions_created: int
    revisions_appended: int
    deletions_recorded: int
    events_replayed: int
    tombstone_blocked: int
    pages_fetched: int
    has_more: bool
    source_event_watermark: datetime
    logical_requests: int
    attempts_made: int
    retries: int
    checkpoint_advanced: bool

    def __post_init__(self) -> None:
        _require_aware(self.source_event_watermark, "source_event_watermark")
        for field, value in (
            ("sessions_created", self.sessions_created),
            ("revisions_appended", self.revisions_appended),
            ("deletions_recorded", self.deletions_recorded),
            ("events_replayed", self.events_replayed),
            ("tombstone_blocked", self.tombstone_blocked),
            ("pages_fetched", self.pages_fetched),
            ("logical_requests", self.logical_requests),
            ("attempts_made", self.attempts_made),
            ("retries", self.retries),
        ):
            _require_nonnegative_int(value, field)
        if self.pages_fetched < 1:
            raise ValueError("a sync outcome must describe at least one page")
        if self.logical_requests < self.pages_fetched:
            raise ValueError("logical requests cannot be fewer than fetched pages")
        if self.attempts_made != self.logical_requests + self.retries:
            raise ValueError("attempts must equal logical requests plus retries")
        if self.status is DetailedTrainingSyncStatus.SYNCED and self.has_more:
            raise ValueError("synced outcome cannot have additional source pages")
        if self.status is DetailedTrainingSyncStatus.SYNCED and not self.checkpoint_advanced:
            raise ValueError("synced outcome must advance its checkpoint")
        if self.status is DetailedTrainingSyncStatus.INCOMPLETE and not self.has_more:
            raise ValueError("incomplete outcome must report additional source pages")
        if self.status is DetailedTrainingSyncStatus.INCOMPLETE and self.checkpoint_advanced:
            raise ValueError("incomplete outcome cannot advance its checkpoint")


@dataclass(frozen=True)
class StoredDetailedTrainingSession:
    revision_id: UUID
    user_id: UUID
    session: DetailedTrainingSession
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.ingested_at, "ingested_at")


@dataclass(frozen=True)
class DetailedTrainingSessionSummary:
    revision_id: UUID
    source_system: DetailedTrainingSourceSystem
    source_session_id: str
    source_revision: str
    title: str
    started_at: datetime
    ended_at: datetime
    exercise_count: int
    set_count: int


@dataclass(frozen=True)
class DetailedTrainingSessionPage:
    """Bounded latest-active full revisions for analytics reads."""

    sessions: tuple[StoredDetailedTrainingSession, ...]
    has_more: bool


@dataclass(frozen=True)
class DetailedTrainingImportOutcome:
    accepted_revisions: int
    duplicate_revisions: int
    applied_deletions: int
    duplicate_deletions: int
    blocked_by_tombstone: int
    sessions_created: int = 0
    revisions_appended: int = 0


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_nonnegative_int(value: int, field: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")


def _require_finite_decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_nonnegative_decimal(value: Decimal, field: str) -> None:
    _require_finite_decimal(value, field)
    if value < 0:
        raise ValueError(f"{field} must be nonnegative")


__all__ = [
    "DetailedTrainingDeletion",
    "DetailedTrainingImportBatch",
    "DetailedTrainingImportOutcome",
    "DetailedTrainingSession",
    "DetailedTrainingSessionSummary",
    "DetailedTrainingSessionPage",
    "DetailedTrainingSourceFetch",
    "DetailedTrainingSourceSystem",
    "DetailedTrainingSyncCheckpoint",
    "DetailedTrainingSyncMode",
    "DetailedTrainingSyncOutcome",
    "DetailedTrainingSyncStatus",
    "DetailedTrainingSet",
    "ExercisePerformance",
    "StoredDetailedTrainingSession",
    "TrainingDistance",
    "TrainingDistanceUnit",
    "TrainingLoad",
    "TrainingLoadUnit",
    "TrainingSetType",
]
