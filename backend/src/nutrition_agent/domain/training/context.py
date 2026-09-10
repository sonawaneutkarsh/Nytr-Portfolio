"""Versioned, deterministic training-day qualification from workout facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from uuid import UUID

from nutrition_agent.domain.training import (
    InvalidDailyHealthTimezone,
    StoredTrainingSession,
    TrainingSourceIdentity,
    TrainingSourceSystem,
    local_day_utc_bounds,
)


class HealthKitWorkoutActivityType(IntEnum):
    """Apple SDK raw values relevant to the initial training-day policy audit.

    Values are pinned from ``HKWorkoutActivityType`` in the installed iOS 26.5
    SDK. The raw source value remains stored unchanged; this enum is only a
    deterministic decoder for policy evaluation and diagnostics.
    """

    CROSS_TRAINING = 11
    FUNCTIONAL_STRENGTH_TRAINING = 20
    PREPARATION_AND_RECOVERY = 33
    RUNNING = 37
    TRADITIONAL_STRENGTH_TRAINING = 50
    WALKING = 52
    FLEXIBILITY = 62


class NormalizedWorkoutKind(StrEnum):
    FUNCTIONAL_STRENGTH_TRAINING = "functional_strength_training"
    TRADITIONAL_STRENGTH_TRAINING = "traditional_strength_training"
    OTHER = "other"
    UNKNOWN = "unknown"


class TrainingDayAttribution(StrEnum):
    START_TIME = "start_time"


class TrainingDayReasonCode(StrEnum):
    QUALIFYING_STRENGTH_SESSION = "qualifying_strength_session"
    NO_SESSIONS = "no_sessions"
    SESSIONS_BELOW_DURATION = "sessions_below_duration"
    UNSUPPORTED_ACTIVITY_TYPE = "unsupported_activity_type"
    MISSING_DURATION = "missing_duration"
    TOMBSTONED_SESSION = "tombstoned_session"


@dataclass(frozen=True)
class TrainingDayPolicy:
    """Immutable product policy; it makes no physiological-benefit claim."""

    version: str
    qualifying_workout_kinds: frozenset[NormalizedWorkoutKind]
    minimum_duration_seconds: Decimal
    attribution: TrainingDayAttribution

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("training-day policy version is required")
        kinds = frozenset(self.qualifying_workout_kinds)
        if not kinds or any(not isinstance(kind, NormalizedWorkoutKind) for kind in kinds):
            raise ValueError("qualifying workout kinds must be explicit normalized values")
        if not isinstance(self.minimum_duration_seconds, Decimal):
            raise TypeError("minimum duration must be Decimal")
        if not self.minimum_duration_seconds.is_finite() or self.minimum_duration_seconds <= 0:
            raise ValueError("minimum duration must be a positive finite Decimal")
        if self.attribution is not TrainingDayAttribution.START_TIME:
            raise ValueError("unsupported training-day attribution rule")
        object.__setattr__(self, "qualifying_workout_kinds", kinds)


OWNER_STRENGTH_TRAINING_DAY_POLICY_V1 = TrainingDayPolicy(
    version="owner-strength-training-day.v1",
    qualifying_workout_kinds=frozenset(
        {
            NormalizedWorkoutKind.FUNCTIONAL_STRENGTH_TRAINING,
            NormalizedWorkoutKind.TRADITIONAL_STRENGTH_TRAINING,
        }
    ),
    minimum_duration_seconds=Decimal("1200"),
    attribution=TrainingDayAttribution.START_TIME,
)


@dataclass(frozen=True)
class TrainingSessionQualification:
    session_id: UUID
    source_identity: TrainingSourceIdentity
    raw_activity_type: str | None
    normalized_kind: NormalizedWorkoutKind
    active_duration_seconds: Decimal | None
    qualifies: bool
    reason_code: TrainingDayReasonCode


@dataclass(frozen=True)
class TrainingSourceSummary:
    source_system: TrainingSourceSystem
    source_name: str | None
    source_bundle_id: str | None
    qualifying_session_count: int


@dataclass(frozen=True)
class QualifyingTrainingSessionEvidence:
    """Minimal factual timing evidence retained for downstream context selection."""

    session_id: UUID
    source_identity: TrainingSourceIdentity
    normalized_kind: NormalizedWorkoutKind
    started_at: datetime
    ended_at: datetime
    active_duration_seconds: Decimal

    def __post_init__(self) -> None:
        for field_name, value in (("started_at", self.started_at), ("ended_at", self.ended_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at precedes started_at")
        if not isinstance(self.active_duration_seconds, Decimal):
            raise TypeError("active_duration_seconds must be a Decimal")
        if not self.active_duration_seconds.is_finite() or self.active_duration_seconds <= Decimal(
            "0"
        ):
            raise ValueError("active_duration_seconds must be a positive finite Decimal")


@dataclass(frozen=True)
class TrainingDayContext:
    local_date: date
    timezone: str
    policy_version: str
    is_training_day: bool
    qualifying_session_count: int
    qualifying_duration_seconds: Decimal
    qualifying_sessions: tuple[QualifyingTrainingSessionEvidence, ...]
    qualifying_session_ids: tuple[UUID, ...]
    qualifying_source_identities: tuple[TrainingSourceIdentity, ...]
    source_summary: tuple[TrainingSourceSummary, ...]
    reason_codes: tuple[TrainingDayReasonCode, ...]


def decode_healthkit_activity_type(raw_value: str | None) -> HealthKitWorkoutActivityType | None:
    """Decode audited Apple raw values without coercing malformed source text."""

    if raw_value is None or not raw_value or raw_value != raw_value.strip():
        return None
    try:
        integer = int(raw_value)
    except ValueError:
        return None
    if str(integer) != raw_value:
        return None
    try:
        return HealthKitWorkoutActivityType(integer)
    except ValueError:
        return None


def normalized_workout_kind(session: StoredTrainingSession) -> NormalizedWorkoutKind:
    """Map only explicit HealthKit strength types to qualifying kinds."""

    if session.source_system is not TrainingSourceSystem.HEALTHKIT:
        return NormalizedWorkoutKind.UNKNOWN
    decoded = decode_healthkit_activity_type(session.activity_type)
    if decoded is HealthKitWorkoutActivityType.FUNCTIONAL_STRENGTH_TRAINING:
        return NormalizedWorkoutKind.FUNCTIONAL_STRENGTH_TRAINING
    if decoded is HealthKitWorkoutActivityType.TRADITIONAL_STRENGTH_TRAINING:
        return NormalizedWorkoutKind.TRADITIONAL_STRENGTH_TRAINING
    if (
        session.activity_type is None
        or decoded is None
        and not _is_canonical_integer(session.activity_type)
    ):
        return NormalizedWorkoutKind.UNKNOWN
    return NormalizedWorkoutKind.OTHER


def qualify_training_session(
    session: StoredTrainingSession,
    policy: TrainingDayPolicy = OWNER_STRENGTH_TRAINING_DAY_POLICY_V1,
) -> TrainingSessionQualification:
    """Qualify one observation using activity type and active duration only."""

    identity = TrainingSourceIdentity(session.source_system, session.source_record_id)
    kind = normalized_workout_kind(session)
    if session.tombstoned_at is not None:
        return TrainingSessionQualification(
            session.session_id,
            identity,
            session.activity_type,
            kind,
            session.active_duration_seconds,
            False,
            TrainingDayReasonCode.TOMBSTONED_SESSION,
        )
    if kind not in policy.qualifying_workout_kinds:
        return TrainingSessionQualification(
            session.session_id,
            identity,
            session.activity_type,
            kind,
            session.active_duration_seconds,
            False,
            TrainingDayReasonCode.UNSUPPORTED_ACTIVITY_TYPE,
        )
    if session.active_duration_seconds is None:
        return TrainingSessionQualification(
            session.session_id,
            identity,
            session.activity_type,
            kind,
            None,
            False,
            TrainingDayReasonCode.MISSING_DURATION,
        )
    if session.active_duration_seconds < policy.minimum_duration_seconds:
        return TrainingSessionQualification(
            session.session_id,
            identity,
            session.activity_type,
            kind,
            session.active_duration_seconds,
            False,
            TrainingDayReasonCode.SESSIONS_BELOW_DURATION,
        )
    return TrainingSessionQualification(
        session.session_id,
        identity,
        session.activity_type,
        kind,
        session.active_duration_seconds,
        True,
        TrainingDayReasonCode.QUALIFYING_STRENGTH_SESSION,
    )


def evaluate_training_day_context(
    *,
    local_date: date,
    timezone: str,
    training_sessions: tuple[StoredTrainingSession, ...],
    policy: TrainingDayPolicy = OWNER_STRENGTH_TRAINING_DAY_POLICY_V1,
) -> TrainingDayContext:
    """Evaluate one local day using the session start time and exact identities."""

    if not isinstance(timezone, str) or not timezone.strip():
        raise InvalidDailyHealthTimezone("timezone is required")
    start, end = local_day_utc_bounds(local_date, timezone)
    sessions = tuple(
        session
        for session in _deduplicate_by_source_identity(training_sessions)
        if session.tombstoned_at is None and start <= session.started_at.astimezone(UTC) < end
    )
    qualifications = tuple(qualify_training_session(session, policy) for session in sessions)
    qualifying = tuple(item for item in qualifications if item.qualifies)

    reasons: tuple[TrainingDayReasonCode, ...]
    if not sessions:
        reasons = (TrainingDayReasonCode.NO_SESSIONS,)
    else:
        present = {item.reason_code for item in qualifications}
        reasons = tuple(reason for reason in TrainingDayReasonCode if reason in present)

    source_counts: dict[tuple[TrainingSourceSystem, str | None, str | None], int] = {}
    qualifying_by_id = {item.session_id: item for item in qualifying}
    qualifying_sessions = tuple(
        session for session in sessions if session.session_id in qualifying_by_id
    )
    for session in qualifying_sessions:
        source_key = (session.source_system, session.source_name, session.source_bundle_id)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1

    source_summary = tuple(
        TrainingSourceSummary(source_system, source_name, source_bundle_id, count)
        for (source_system, source_name, source_bundle_id), count in sorted(
            source_counts.items(),
            key=lambda item: (
                item[0][0].value,
                item[0][1] or "",
                item[0][2] or "",
            ),
        )
    )
    identities = tuple(sorted(item.source_identity for item in qualifying))
    session_ids = tuple(item.session_id for item in qualifying)
    evidence = tuple(
        _qualifying_session_evidence(session, qualifying_by_id[session.session_id])
        for session in qualifying_sessions
    )
    duration = sum(
        (item.active_duration_seconds for item in qualifying if item.active_duration_seconds),
        Decimal("0"),
    )
    return TrainingDayContext(
        local_date=local_date,
        timezone=timezone,
        policy_version=policy.version,
        is_training_day=bool(qualifying),
        qualifying_session_count=len(qualifying),
        qualifying_duration_seconds=duration,
        qualifying_sessions=evidence,
        qualifying_session_ids=session_ids,
        qualifying_source_identities=identities,
        source_summary=source_summary,
        reason_codes=reasons,
    )


def _qualifying_session_evidence(
    session: StoredTrainingSession,
    qualification: TrainingSessionQualification,
) -> QualifyingTrainingSessionEvidence:
    duration = qualification.active_duration_seconds
    if duration is None:
        raise ValueError("qualifying session must retain its observed duration")
    return QualifyingTrainingSessionEvidence(
        session_id=session.session_id,
        source_identity=qualification.source_identity,
        normalized_kind=qualification.normalized_kind,
        started_at=session.started_at,
        ended_at=session.ended_at,
        active_duration_seconds=duration,
    )


def _deduplicate_by_source_identity(
    sessions: tuple[StoredTrainingSession, ...],
) -> tuple[StoredTrainingSession, ...]:
    grouped: dict[TrainingSourceIdentity, list[StoredTrainingSession]] = {}
    for session in sessions:
        identity = TrainingSourceIdentity(session.source_system, session.source_record_id)
        grouped.setdefault(identity, []).append(session)

    selected: list[StoredTrainingSession] = []
    for identity in sorted(grouped):
        candidates = grouped[identity]
        tombstoned = [item for item in candidates if item.tombstoned_at is not None]
        pool = tombstoned or candidates
        selected.append(
            min(
                pool,
                key=lambda item: (
                    item.started_at.astimezone(UTC),
                    str(item.session_id),
                ),
            )
        )
    return tuple(
        sorted(
            selected,
            key=lambda item: (
                item.started_at.astimezone(UTC),
                item.source_system.value,
                item.source_record_id,
                str(item.session_id),
            ),
        )
    )


def _is_canonical_integer(value: str) -> bool:
    try:
        parsed = int(value)
    except ValueError:
        return False
    return str(parsed) == value


__all__ = [
    "HealthKitWorkoutActivityType",
    "NormalizedWorkoutKind",
    "OWNER_STRENGTH_TRAINING_DAY_POLICY_V1",
    "QualifyingTrainingSessionEvidence",
    "TrainingDayAttribution",
    "TrainingDayContext",
    "TrainingDayPolicy",
    "TrainingDayReasonCode",
    "TrainingSessionQualification",
    "TrainingSourceSummary",
    "decode_healthkit_activity_type",
    "evaluate_training_day_context",
    "normalized_workout_kind",
    "qualify_training_session",
]
