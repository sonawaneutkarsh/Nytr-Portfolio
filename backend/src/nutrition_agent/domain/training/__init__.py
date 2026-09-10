"""Immutable, source-identified training observations and daily summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nutrition_agent.domain.health.trend import BodyMassObservation


class TrainingSourceSystem(StrEnum):
    """Source systems supported by the M12A ingestion contract."""

    HEALTHKIT = "healthkit"


@dataclass(frozen=True)
class TrainingSessionObservation:
    """One complete immutable workout observation from its source system."""

    source_system: TrainingSourceSystem
    source_record_id: str
    activity_type: str | None
    started_at: datetime
    ended_at: datetime
    active_duration_seconds: Decimal | None
    active_energy_kcal: Decimal | None
    timezone_identifier: str | None
    source_name: str | None
    source_bundle_id: str | None
    source_revision: str | None

    def __post_init__(self) -> None:
        if not self.source_record_id:
            raise ValueError("source_record_id must not be empty")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at precedes started_at")
        _require_optional_nonnegative(self.active_duration_seconds, "active_duration_seconds")
        _require_optional_nonnegative(self.active_energy_kcal, "active_energy_kcal")


@dataclass(frozen=True)
class TrainingSessionDeletion:
    """One source deletion identity; no observation values are invented."""

    source_system: TrainingSourceSystem
    source_record_id: str

    def __post_init__(self) -> None:
        if not self.source_record_id:
            raise ValueError("source_record_id must not be empty")


@dataclass(frozen=True)
class TrainingSyncBatch:
    client_batch_id: UUID
    added: tuple[TrainingSessionObservation, ...]
    deletions: tuple[TrainingSessionDeletion, ...]


@dataclass(frozen=True)
class TrainingBatchOutcome:
    accepted_added: int
    duplicate_added: int
    applied_deletions: int
    duplicate_deletions: int


@dataclass(frozen=True)
class StoredTrainingSession:
    """Durable complete observation. Tombstoning never rewrites its meaning."""

    session_id: UUID
    user_id: UUID
    source_system: TrainingSourceSystem
    source_record_id: str
    activity_type: str | None
    started_at: datetime
    ended_at: datetime
    active_duration_seconds: Decimal | None
    active_energy_kcal: Decimal | None
    timezone_identifier: str | None
    source_name: str | None
    source_bundle_id: str | None
    source_revision: str | None
    ingested_at: datetime
    tombstoned_at: datetime | None

    def __post_init__(self) -> None:
        TrainingSessionObservation(
            source_system=self.source_system,
            source_record_id=self.source_record_id,
            activity_type=self.activity_type,
            started_at=self.started_at,
            ended_at=self.ended_at,
            active_duration_seconds=self.active_duration_seconds,
            active_energy_kcal=self.active_energy_kcal,
            timezone_identifier=self.timezone_identifier,
            source_name=self.source_name,
            source_bundle_id=self.source_bundle_id,
            source_revision=self.source_revision,
        )
        _require_aware(self.ingested_at, "ingested_at")
        if self.tombstoned_at is not None:
            _require_aware(self.tombstoned_at, "tombstoned_at")


@dataclass(frozen=True, order=True)
class TrainingSourceIdentity:
    source_system: TrainingSourceSystem
    source_record_id: str


@dataclass(frozen=True)
class DailyHealthSummary:
    """Pure factual evidence for one requested local calendar date.

    ``workout_count`` is ``None`` when no readable workout observation exists;
    absence is not converted to an authoritative zero because HealthKit read
    authorization remains opaque.
    """

    local_date: date
    timezone: str
    latest_body_mass: BodyMassObservation | None
    workout_count: int | None
    total_workout_duration_seconds: Decimal | None
    total_workout_active_energy_kcal: Decimal | None
    training_sources: tuple[TrainingSourceIdentity, ...]
    latest_training_ingested_at: datetime | None


class InvalidDailyHealthTimezone(ValueError):
    """The requested timezone is not a recognized IANA timezone."""


def local_day_utc_bounds(local_date: date, timezone: str) -> tuple[datetime, datetime]:
    """Return the half-open UTC range for one IANA-local calendar date."""

    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidDailyHealthTimezone("timezone is not recognized") from exc
    start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def summarize_daily_health(
    *,
    local_date: date,
    timezone: str,
    body_mass_observations: tuple[BodyMassObservation, ...],
    training_sessions: tuple[StoredTrainingSession, ...],
) -> DailyHealthSummary:
    """Derive one deterministic summary without mutating planner or targets."""

    start, end = local_day_utc_bounds(local_date, timezone)
    body_mass = tuple(
        observation
        for observation in body_mass_observations
        if start <= observation.measured_at.astimezone(UTC) < end
    )
    active_sessions = tuple(
        session
        for session in training_sessions
        if session.tombstoned_at is None and start <= session.started_at.astimezone(UTC) < end
    )
    latest_body_mass = max(
        body_mass,
        key=lambda item: (item.measured_at.astimezone(UTC), str(item.sample_uuid)),
        default=None,
    )

    if not active_sessions:
        return DailyHealthSummary(
            local_date=local_date,
            timezone=timezone,
            latest_body_mass=latest_body_mass,
            workout_count=None,
            total_workout_duration_seconds=None,
            total_workout_active_energy_kcal=None,
            training_sources=(),
            latest_training_ingested_at=None,
        )

    durations = tuple(session.active_duration_seconds for session in active_sessions)
    energies = tuple(session.active_energy_kcal for session in active_sessions)
    source_identities = tuple(
        sorted(
            {
                TrainingSourceIdentity(session.source_system, session.source_record_id)
                for session in active_sessions
            }
        )
    )
    return DailyHealthSummary(
        local_date=local_date,
        timezone=timezone,
        latest_body_mass=latest_body_mass,
        workout_count=len(active_sessions),
        total_workout_duration_seconds=(
            sum((value for value in durations if value is not None), Decimal(0))
            if all(value is not None for value in durations)
            else None
        ),
        total_workout_active_energy_kcal=(
            sum((value for value in energies if value is not None), Decimal(0))
            if all(value is not None for value in energies)
            else None
        ),
        training_sources=source_identities,
        latest_training_ingested_at=max(session.ingested_at for session in active_sessions),
    )


def has_readable_workout_on_local_date(summary: DailyHealthSummary) -> bool:
    """Factual only: did at least one readable workout observation exist?"""

    return summary.workout_count is not None and summary.workout_count > 0


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_optional_nonnegative(value: Decimal | None, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a nonnegative finite Decimal")


__all__ = [
    "DailyHealthSummary",
    "InvalidDailyHealthTimezone",
    "StoredTrainingSession",
    "TrainingBatchOutcome",
    "TrainingSessionDeletion",
    "TrainingSessionObservation",
    "TrainingSourceIdentity",
    "TrainingSourceSystem",
    "TrainingSyncBatch",
    "has_readable_workout_on_local_date",
    "local_day_utc_bounds",
    "summarize_daily_health",
]
