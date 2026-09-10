"""Pure factual analytics and advisory coaching over immutable training revisions.

Neither projection qualifies training days or alters Hevy, nutrition, or plans.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nutrition_agent.domain.training.detail import (
    DetailedTrainingSession,
    DetailedTrainingSet,
    DetailedTrainingSourceSystem,
    ExercisePerformance,
    StoredDetailedTrainingSession,
    TrainingSetType,
)


class ExerciseMetricFamily(StrEnum):
    REP_LOAD = "rep_load"
    BODYWEIGHT_REP = "bodyweight_rep"
    ASSISTED_REP = "assisted_rep"
    DURATION = "duration"
    DISTANCE = "distance"
    OTHER = "other"


class MetricCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class TrainingPRType(StrEnum):
    HIGHEST_LOAD = "highest_load_for_exercise"
    MOST_REPS_AT_LOAD = "most_reps_at_same_load"
    HIGHEST_VOLUME_SESSION = "highest_valid_volume_session"


class TrainingCoachingStatus(StrEnum):
    PROGRESS = "progress"
    HOLD = "hold"
    UNAVAILABLE = "unavailable"


class TrainingCoachingAction(StrEnum):
    ADD_ONE_TOP_SET_REP_SAME_LOAD = "add_one_top_set_rep_same_load"
    ADD_ONE_TOTAL_REP_BODYWEIGHT = "add_one_total_rep_bodyweight"
    ADD_ONE_TOTAL_REP_SAME_ASSISTANCE = "add_one_total_rep_same_assistance"
    REPEAT_LATEST_TOP_SET = "repeat_latest_top_set"
    REPEAT_LATEST_REP_TOTAL = "repeat_latest_rep_total"


@dataclass(frozen=True)
class TrainingAnalyticsPolicy:
    version: str
    working_set_types: frozenset[TrainingSetType]
    warmup_set_types: frozenset[TrainingSetType]
    assisted_source_exercise_ids: frozenset[str] = frozenset()
    assisted_display_name_suffixes: tuple[str, ...] = ("(assisted)",)


OWNER_TRAINING_ANALYTICS_V1 = TrainingAnalyticsPolicy(
    version="owner-training-analytics.v1",
    working_set_types=frozenset(
        {TrainingSetType.NORMAL, TrainingSetType.DROPSET, TrainingSetType.FAILURE}
    ),
    warmup_set_types=frozenset({TrainingSetType.WARMUP}),
)


@dataclass(frozen=True)
class TrainingCoachingPolicy:
    version: str
    max_evidence_age_days: int
    high_effort_rpe: Decimal
    max_top_set_reps: int
    max_total_reps: int


OWNER_TRAINING_COACHING_V1 = TrainingCoachingPolicy(
    version="owner-training-coaching.v1",
    max_evidence_age_days=42,
    high_effort_rpe=Decimal("9"),
    max_top_set_reps=30,
    max_total_reps=100,
)


@dataclass(frozen=True)
class TrainingCoachingTarget:
    working_set_count: int
    top_load_kg: Decimal | None = None
    top_set_reps: int | None = None
    total_reps: int | None = None
    keep_assistance_constant: bool = False


@dataclass(frozen=True)
class ExerciseCoachingGuidance:
    policy_version: str
    analytics_policy_version: str
    timezone: str
    as_of_date: date
    status: TrainingCoachingStatus
    action: TrainingCoachingAction | None
    metric_family: ExerciseMetricFamily | None
    latest_revision_id: UUID | None
    previous_revision_id: UUID | None
    latest_started_at: datetime | None
    previous_started_at: datetime | None
    target: TrainingCoachingTarget | None
    reason_codes: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class TopLoadSetEvidence:
    set_identity: str
    set_index: int
    set_type: TrainingSetType
    reps: int
    load_kg: Decimal
    rpe: Decimal | None


@dataclass(frozen=True)
class ExerciseOccurrenceAnalytics:
    occurrence_identity: str
    source_exercise_id: str
    display_name: str
    exercise_order: int
    metric_family: ExerciseMetricFamily
    recorded_set_count: int
    working_set_count: int
    warmup_set_count: int
    unsupported_set_count: int
    rep_total: int | None
    max_load_kg: Decimal | None
    top_load_set: TopLoadSetEvidence | None
    volume_kg_reps: Decimal | None
    duration_seconds: Decimal | None
    distance_meters: Decimal | None
    max_rpe: Decimal | None
    metric_completeness: MetricCompleteness
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class TrainingSessionAnalytics:
    policy_version: str
    revision_id: UUID
    source_system: DetailedTrainingSourceSystem
    source_session_id: str
    source_revision: str
    title: str
    started_at: datetime
    ended_at: datetime
    session_duration_seconds: Decimal
    exercise_count: int
    recorded_set_count: int
    working_set_count: int
    warmup_set_count: int
    unsupported_set_count: int
    rep_total: int | None
    volume_kg_reps: Decimal | None
    volume_exercise_count: int
    exercises: tuple[ExerciseOccurrenceAnalytics, ...]


@dataclass(frozen=True)
class ExerciseHistoryPoint:
    revision_id: UUID
    source_session_id: str
    source_revision: str
    session_title: str
    started_at: datetime
    display_name: str
    occurrence_count: int
    metric_family: ExerciseMetricFamily
    recorded_set_count: int
    working_set_count: int
    warmup_set_count: int
    unsupported_set_count: int
    rep_total: int | None
    max_load_kg: Decimal | None
    top_load_set: TopLoadSetEvidence | None
    volume_kg_reps: Decimal | None
    max_rpe: Decimal | None
    metric_completeness: MetricCompleteness


@dataclass(frozen=True)
class ExerciseSessionComparison:
    latest_revision_id: UUID
    previous_revision_id: UUID | None
    working_set_count_delta: int | None
    top_load_delta_kg: Decimal | None
    reps_at_same_top_load_delta: int | None
    volume_delta_kg_reps: Decimal | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ExerciseFrequency:
    timezone: str
    as_of_date: date
    sessions_last_7_days: int
    sessions_last_28_days: int
    days_since_last_performance: int | None


@dataclass(frozen=True)
class TrainingPREvidence:
    evidence_id: str
    pr_type: TrainingPRType
    revision_id: UUID
    source_session_id: str
    observed_at: datetime
    load_kg: Decimal | None
    reps: int | None
    volume_kg_reps: Decimal | None
    scope: str


@dataclass(frozen=True)
class TrainingHistoryCompleteness:
    source_bootstrap_complete: bool
    query_complete: bool
    lifetime_guaranteed: bool
    wording: str


@dataclass(frozen=True)
class ExerciseTrainingHistory:
    policy_version: str
    source_system: DetailedTrainingSourceSystem
    source_exercise_id: str
    latest_display_name: str
    history: tuple[ExerciseHistoryPoint, ...]
    latest: ExerciseHistoryPoint
    previous: ExerciseHistoryPoint | None
    comparison: ExerciseSessionComparison
    frequency: ExerciseFrequency
    pr_evidence: tuple[TrainingPREvidence, ...]
    completeness: TrainingHistoryCompleteness
    coaching: ExerciseCoachingGuidance


@dataclass(frozen=True)
class ExerciseIndexEntry:
    source_system: DetailedTrainingSourceSystem
    source_exercise_id: str
    latest_display_name: str
    last_performed_at: datetime
    session_count: int
    latest_metric_family: ExerciseMetricFamily
    latest_top_load_set: TopLoadSetEvidence | None
    frequency: ExerciseFrequency


class InvalidTrainingAnalyticsTimezone(ValueError):
    pass


def analyze_training_session(
    stored: StoredDetailedTrainingSession,
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1,
) -> TrainingSessionAnalytics:
    session = stored.session
    exercises = tuple(analyze_exercise_occurrence(item, policy) for item in session.exercises)
    rep_values = tuple(item.rep_total for item in exercises if _is_rep_family(item.metric_family))
    volumes = tuple(
        item.volume_kg_reps
        for item in exercises
        if item.metric_family is ExerciseMetricFamily.REP_LOAD
    )
    return TrainingSessionAnalytics(
        policy_version=policy.version,
        revision_id=stored.revision_id,
        source_system=session.source_system,
        source_session_id=session.source_session_id,
        source_revision=session.source_revision,
        title=session.title,
        started_at=session.started_at,
        ended_at=session.ended_at,
        session_duration_seconds=_duration_decimal(session),
        exercise_count=len(exercises),
        recorded_set_count=sum(item.recorded_set_count for item in exercises),
        working_set_count=sum(item.working_set_count for item in exercises),
        warmup_set_count=sum(item.warmup_set_count for item in exercises),
        unsupported_set_count=sum(item.unsupported_set_count for item in exercises),
        rep_total=(
            sum(value for value in rep_values if value is not None)
            if rep_values and all(value is not None for value in rep_values)
            else None
        ),
        volume_kg_reps=(
            sum((value for value in volumes if value is not None), Decimal(0))
            if volumes and all(value is not None for value in volumes)
            else None
        ),
        volume_exercise_count=sum(value is not None for value in volumes),
        exercises=exercises,
    )


def analyze_exercise_occurrence(
    exercise: ExercisePerformance,
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1,
) -> ExerciseOccurrenceAnalytics:
    working = tuple(item for item in exercise.sets if item.set_type in policy.working_set_types)
    warmups = tuple(item for item in exercise.sets if item.set_type in policy.warmup_set_types)
    unsupported = tuple(
        item
        for item in exercise.sets
        if item.set_type not in policy.working_set_types
        and item.set_type not in policy.warmup_set_types
    )
    family = classify_exercise_modality(exercise, working, policy)
    rep_total = _rep_total(working, family)
    valid_rep_load = tuple(item for item in working if _valid_rep_load(item))
    top = (
        max(
            (_top_set(item) for item in valid_rep_load),
            key=lambda item: (item.load_kg, item.reps, -item.set_index),
        )
        if valid_rep_load and family is ExerciseMetricFamily.REP_LOAD
        else None
    )
    volume = _volume(working, family)
    duration = _sum_present(item.duration_seconds for item in working)
    distance = _sum_present(
        item.distance.value if item.distance is not None else None for item in working
    )
    max_rpe = max(
        (item.rpe for item in working if item.rpe is not None),
        default=None,
    )
    reasons: list[str] = []
    if not working:
        reasons.append("no_working_sets")
    if family is ExerciseMetricFamily.BODYWEIGHT_REP:
        reasons.append("bodyweight_volume_unavailable")
    elif family is ExerciseMetricFamily.ASSISTED_REP:
        reasons.append("assistance_is_not_external_load")
    elif family in {ExerciseMetricFamily.DURATION, ExerciseMetricFamily.DISTANCE}:
        reasons.append("non_strength_modality")
    elif family is ExerciseMetricFamily.OTHER:
        reasons.append("unsupported_metric_family")
    if family is ExerciseMetricFamily.REP_LOAD and volume is None:
        reasons.append("incomplete_rep_load_fields")
    if unsupported:
        reasons.append("unsupported_set_type")
    completeness = _metric_completeness(family, working, rep_total, volume, unsupported)
    return ExerciseOccurrenceAnalytics(
        occurrence_identity=exercise.occurrence_identity,
        source_exercise_id=exercise.source_exercise_id,
        display_name=exercise.display_name,
        exercise_order=exercise.exercise_order,
        metric_family=family,
        recorded_set_count=len(exercise.sets),
        working_set_count=len(working),
        warmup_set_count=len(warmups),
        unsupported_set_count=len(unsupported),
        rep_total=rep_total,
        max_load_kg=top.load_kg if top else None,
        top_load_set=top,
        volume_kg_reps=volume,
        duration_seconds=duration,
        distance_meters=distance,
        max_rpe=max_rpe,
        metric_completeness=completeness,
        reason_codes=tuple(reasons),
    )


def classify_exercise_modality(
    exercise: ExercisePerformance,
    working_sets: tuple[DetailedTrainingSet, ...],
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1,
) -> ExerciseMetricFamily:
    has_reps = any((item.reps or 0) > 0 for item in working_sets)
    has_distance = any(
        item.distance is not None and item.distance.value > 0 for item in working_sets
    )
    has_duration = any(
        item.duration_seconds is not None and item.duration_seconds > 0 for item in working_sets
    )
    assisted = exercise.source_exercise_id in policy.assisted_source_exercise_ids or any(
        item.load is not None and item.load.kilograms < 0 for item in working_sets
    )
    normalized_name = exercise.display_name.strip().casefold()
    assisted = assisted or any(
        normalized_name.endswith(suffix.casefold())
        for suffix in policy.assisted_display_name_suffixes
    )
    if assisted and has_reps:
        return ExerciseMetricFamily.ASSISTED_REP
    if has_distance:
        return ExerciseMetricFamily.DISTANCE
    if has_duration and not has_reps:
        return ExerciseMetricFamily.DURATION
    if has_reps and any(item.load is not None and item.load.kilograms > 0 for item in working_sets):
        return ExerciseMetricFamily.REP_LOAD
    if has_reps:
        return ExerciseMetricFamily.BODYWEIGHT_REP
    return ExerciseMetricFamily.OTHER


def build_exercise_history(
    *,
    sessions: tuple[StoredDetailedTrainingSession, ...],
    source_system: DetailedTrainingSourceSystem,
    source_exercise_id: str,
    timezone: str,
    as_of_date: date,
    result_limit: int,
    source_bootstrap_complete: bool,
    query_complete: bool,
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1,
) -> ExerciseTrainingHistory | None:
    zone = _zone(timezone)
    points = tuple(
        point
        for stored in sessions
        if stored.session.source_system is source_system
        if (point := _history_point(stored, source_exercise_id, policy)) is not None
    )
    ordered = tuple(
        sorted(
            points,
            key=lambda item: (item.started_at, item.source_session_id, item.revision_id),
            reverse=True,
        )
    )
    if not ordered:
        return None
    latest = ordered[0]
    previous = ordered[1] if len(ordered) > 1 else None
    completeness = _history_completeness(source_bootstrap_complete, query_complete)
    return ExerciseTrainingHistory(
        policy_version=policy.version,
        source_system=source_system,
        source_exercise_id=source_exercise_id,
        latest_display_name=latest.display_name,
        history=ordered[:result_limit],
        latest=latest,
        previous=previous,
        comparison=_compare(latest, previous),
        frequency=_frequency(ordered, timezone, zone, as_of_date),
        pr_evidence=_pr_evidence(
            ordered,
            source_system,
            source_exercise_id,
            completeness,
            policy.version,
        ),
        completeness=completeness,
        coaching=_build_training_coaching(
            ordered=ordered,
            sessions=sessions,
            source_exercise_id=source_exercise_id,
            timezone=timezone,
            zone=zone,
            as_of_date=as_of_date,
            completeness=completeness,
            analytics_policy=policy,
        ),
    )


_COACHING_LIMITATIONS = (
    "advisory_only",
    "no_form_recovery_or_pain_evidence",
    "no_program_rep_range_or_equipment_increment",
    "within_synced_hevy_history_not_lifetime",
)


def _build_training_coaching(
    *,
    ordered: tuple[ExerciseHistoryPoint, ...],
    sessions: tuple[StoredDetailedTrainingSession, ...],
    source_exercise_id: str,
    timezone: str,
    zone: ZoneInfo,
    as_of_date: date,
    completeness: TrainingHistoryCompleteness,
    analytics_policy: TrainingAnalyticsPolicy,
    coaching_policy: TrainingCoachingPolicy = OWNER_TRAINING_COACHING_V1,
) -> ExerciseCoachingGuidance:
    latest = ordered[0] if ordered else None
    previous = ordered[1] if len(ordered) > 1 else None

    def unavailable(*reasons: str) -> ExerciseCoachingGuidance:
        return ExerciseCoachingGuidance(
            policy_version=coaching_policy.version,
            analytics_policy_version=analytics_policy.version,
            timezone=timezone,
            as_of_date=as_of_date,
            status=TrainingCoachingStatus.UNAVAILABLE,
            action=None,
            metric_family=latest.metric_family if latest is not None else None,
            latest_revision_id=latest.revision_id if latest is not None else None,
            previous_revision_id=previous.revision_id if previous is not None else None,
            latest_started_at=latest.started_at if latest is not None else None,
            previous_started_at=previous.started_at if previous is not None else None,
            target=None,
            reason_codes=tuple(reasons),
            limitations=_COACHING_LIMITATIONS,
        )

    if not completeness.source_bootstrap_complete or not completeness.query_complete:
        return unavailable("history_incomplete")
    if latest is None or previous is None:
        return unavailable("insufficient_history")
    latest_date = latest.started_at.astimezone(zone).date()
    age_days = (as_of_date - latest_date).days
    if age_days < 0:
        return unavailable("future_evidence")
    if age_days > coaching_policy.max_evidence_age_days:
        return unavailable("stale_latest_performance")
    if latest.occurrence_count != 1 or previous.occurrence_count != 1:
        return unavailable("repeated_exercise_occurrence")
    if latest.metric_family is not previous.metric_family:
        return unavailable("metric_family_changed")
    if latest.metric_family not in {
        ExerciseMetricFamily.REP_LOAD,
        ExerciseMetricFamily.BODYWEIGHT_REP,
        ExerciseMetricFamily.ASSISTED_REP,
    }:
        return unavailable("unsupported_metric_family")
    if (
        latest.metric_completeness is not MetricCompleteness.COMPLETE
        or previous.metric_completeness is not MetricCompleteness.COMPLETE
    ):
        return unavailable("incomplete_metrics")
    if latest.unsupported_set_count or previous.unsupported_set_count:
        return unavailable("unsupported_sets_present")
    if latest.working_set_count < 1 or latest.working_set_count != previous.working_set_count:
        return unavailable("working_set_structure_changed")

    latest_exercise = _exact_exercise_for_revision(sessions, latest.revision_id, source_exercise_id)
    previous_exercise = _exact_exercise_for_revision(
        sessions, previous.revision_id, source_exercise_id
    )
    if latest_exercise is None or previous_exercise is None:
        return unavailable("source_evidence_unavailable")

    latest_working = tuple(
        item for item in latest_exercise.sets if item.set_type in analytics_policy.working_set_types
    )
    previous_working = tuple(
        item
        for item in previous_exercise.sets
        if item.set_type in analytics_policy.working_set_types
    )
    force_hold_reasons: list[str] = []
    if any(
        item.set_type is TrainingSetType.FAILURE for item in (*latest_working, *previous_working)
    ):
        force_hold_reasons.append("failure_set_present")
    if latest.max_rpe is not None and latest.max_rpe > coaching_policy.high_effort_rpe:
        force_hold_reasons.append("high_effort_recorded")

    if latest.metric_family is ExerciseMetricFamily.REP_LOAD:
        return _weighted_coaching(
            latest,
            previous,
            force_hold_reasons,
            coaching_policy,
            analytics_policy,
            timezone,
            as_of_date,
        )
    if latest.rep_total is None or previous.rep_total is None:
        return unavailable("rep_total_unavailable")
    if latest.rep_total < 1 or latest.rep_total > coaching_policy.max_total_reps:
        return unavailable("rep_target_out_of_bounds")
    if latest.metric_family is ExerciseMetricFamily.ASSISTED_REP:
        latest_assistance = _assistance_signature(latest_working)
        previous_assistance = _assistance_signature(previous_working)
        if latest_assistance is None or previous_assistance is None:
            return unavailable("assistance_configuration_incomplete")
        if latest_assistance != previous_assistance:
            return unavailable("assistance_configuration_changed")
        return _rep_total_coaching(
            latest,
            previous,
            force_hold_reasons,
            coaching_policy,
            analytics_policy,
            progress_action=TrainingCoachingAction.ADD_ONE_TOTAL_REP_SAME_ASSISTANCE,
            keep_assistance_constant=True,
            timezone=timezone,
            as_of_date=as_of_date,
        )
    return _rep_total_coaching(
        latest,
        previous,
        force_hold_reasons,
        coaching_policy,
        analytics_policy,
        progress_action=TrainingCoachingAction.ADD_ONE_TOTAL_REP_BODYWEIGHT,
        keep_assistance_constant=False,
        timezone=timezone,
        as_of_date=as_of_date,
    )


def _weighted_coaching(
    latest: ExerciseHistoryPoint,
    previous: ExerciseHistoryPoint,
    force_hold_reasons: list[str],
    coaching_policy: TrainingCoachingPolicy,
    analytics_policy: TrainingAnalyticsPolicy,
    timezone: str,
    as_of_date: date,
) -> ExerciseCoachingGuidance:
    latest_top, previous_top = latest.top_load_set, previous.top_load_set
    if latest_top is None or previous_top is None:
        return _coaching_unavailable_from_points(
            latest,
            previous,
            coaching_policy,
            analytics_policy,
            timezone,
            as_of_date,
            "top_set_unavailable",
        )
    if latest_top.reps < 1 or latest_top.reps > coaching_policy.max_top_set_reps:
        return _coaching_unavailable_from_points(
            latest,
            previous,
            coaching_policy,
            analytics_policy,
            timezone,
            as_of_date,
            "rep_target_out_of_bounds",
        )
    hold_reasons = list(force_hold_reasons)
    if latest_top.load_kg > previous_top.load_kg:
        hold_reasons.append("load_increased_consolidate")
    elif latest_top.load_kg < previous_top.load_kg:
        hold_reasons.append("load_regressed")
    elif latest_top.reps < previous_top.reps:
        hold_reasons.append("top_set_reps_regressed")
    elif latest_top.reps >= coaching_policy.max_top_set_reps:
        hold_reasons.append("rep_target_bound_reached")
    target_reps = latest_top.reps if hold_reasons else latest_top.reps + 1
    return _coaching_result(
        latest=latest,
        previous=previous,
        policy=coaching_policy,
        analytics_policy=analytics_policy,
        timezone=timezone,
        as_of_date=as_of_date,
        status=(TrainingCoachingStatus.HOLD if hold_reasons else TrainingCoachingStatus.PROGRESS),
        action=(
            TrainingCoachingAction.REPEAT_LATEST_TOP_SET
            if hold_reasons
            else TrainingCoachingAction.ADD_ONE_TOP_SET_REP_SAME_LOAD
        ),
        target=TrainingCoachingTarget(
            working_set_count=latest.working_set_count,
            top_load_kg=latest_top.load_kg,
            top_set_reps=target_reps,
        ),
        reason_codes=tuple(hold_reasons or ("comparable_performance_supports_rep_progression",)),
    )


def _rep_total_coaching(
    latest: ExerciseHistoryPoint,
    previous: ExerciseHistoryPoint,
    force_hold_reasons: list[str],
    coaching_policy: TrainingCoachingPolicy,
    analytics_policy: TrainingAnalyticsPolicy,
    *,
    progress_action: TrainingCoachingAction,
    keep_assistance_constant: bool,
    timezone: str,
    as_of_date: date,
) -> ExerciseCoachingGuidance:
    assert latest.rep_total is not None
    assert previous.rep_total is not None
    hold_reasons = list(force_hold_reasons)
    if latest.rep_total < previous.rep_total:
        hold_reasons.append("total_reps_regressed")
    elif latest.rep_total >= coaching_policy.max_total_reps:
        hold_reasons.append("rep_target_bound_reached")
    target_reps = latest.rep_total if hold_reasons else latest.rep_total + 1
    return _coaching_result(
        latest=latest,
        previous=previous,
        policy=coaching_policy,
        analytics_policy=analytics_policy,
        timezone=timezone,
        as_of_date=as_of_date,
        status=(TrainingCoachingStatus.HOLD if hold_reasons else TrainingCoachingStatus.PROGRESS),
        action=(
            TrainingCoachingAction.REPEAT_LATEST_REP_TOTAL if hold_reasons else progress_action
        ),
        target=TrainingCoachingTarget(
            working_set_count=latest.working_set_count,
            total_reps=target_reps,
            keep_assistance_constant=keep_assistance_constant,
        ),
        reason_codes=tuple(hold_reasons or ("comparable_performance_supports_rep_progression",)),
    )


def _coaching_unavailable_from_points(
    latest: ExerciseHistoryPoint,
    previous: ExerciseHistoryPoint,
    policy: TrainingCoachingPolicy,
    analytics_policy: TrainingAnalyticsPolicy,
    timezone: str,
    as_of_date: date,
    reason: str,
) -> ExerciseCoachingGuidance:
    return _coaching_result(
        latest=latest,
        previous=previous,
        policy=policy,
        analytics_policy=analytics_policy,
        timezone=timezone,
        as_of_date=as_of_date,
        status=TrainingCoachingStatus.UNAVAILABLE,
        action=None,
        target=None,
        reason_codes=(reason,),
    )


def _coaching_result(
    *,
    latest: ExerciseHistoryPoint,
    previous: ExerciseHistoryPoint,
    policy: TrainingCoachingPolicy,
    analytics_policy: TrainingAnalyticsPolicy,
    timezone: str,
    as_of_date: date,
    status: TrainingCoachingStatus,
    action: TrainingCoachingAction | None,
    target: TrainingCoachingTarget | None,
    reason_codes: tuple[str, ...],
) -> ExerciseCoachingGuidance:
    return ExerciseCoachingGuidance(
        policy_version=policy.version,
        analytics_policy_version=analytics_policy.version,
        timezone=timezone,
        as_of_date=as_of_date,
        status=status,
        action=action,
        metric_family=latest.metric_family,
        latest_revision_id=latest.revision_id,
        previous_revision_id=previous.revision_id,
        latest_started_at=latest.started_at,
        previous_started_at=previous.started_at,
        target=target,
        reason_codes=reason_codes,
        limitations=_COACHING_LIMITATIONS,
    )


def _exact_exercise_for_revision(
    sessions: tuple[StoredDetailedTrainingSession, ...],
    revision_id: UUID,
    source_exercise_id: str,
) -> ExercisePerformance | None:
    matches = tuple(
        exercise
        for stored in sessions
        if stored.revision_id == revision_id
        for exercise in stored.session.exercises
        if exercise.source_exercise_id == source_exercise_id
    )
    return matches[0] if len(matches) == 1 else None


def _assistance_signature(
    working_sets: tuple[DetailedTrainingSet, ...],
) -> tuple[Decimal, ...] | None:
    if not working_sets or any(item.load is None for item in working_sets):
        return None
    return tuple(item.load.kilograms for item in working_sets if item.load is not None)


def build_exercise_index(
    *,
    sessions: tuple[StoredDetailedTrainingSession, ...],
    timezone: str,
    as_of_date: date,
    result_limit: int,
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1,
) -> tuple[ExerciseIndexEntry, ...]:
    zone = _zone(timezone)
    identities = sorted(
        {
            (stored.session.source_system, exercise.source_exercise_id)
            for stored in sessions
            for exercise in stored.session.exercises
        },
        key=lambda item: (item[0].value, item[1]),
    )
    entries: list[ExerciseIndexEntry] = []
    for source_system, source_exercise_id in identities:
        points = tuple(
            point
            for stored in sessions
            if stored.session.source_system is source_system
            if (point := _history_point(stored, source_exercise_id, policy)) is not None
        )
        ordered = tuple(
            sorted(
                points,
                key=lambda item: (item.started_at, item.source_session_id, item.revision_id),
                reverse=True,
            )
        )
        latest = ordered[0]
        entries.append(
            ExerciseIndexEntry(
                source_system=source_system,
                source_exercise_id=source_exercise_id,
                latest_display_name=latest.display_name,
                last_performed_at=latest.started_at,
                session_count=len(ordered),
                latest_metric_family=latest.metric_family,
                latest_top_load_set=latest.top_load_set,
                frequency=_frequency(ordered, timezone, zone, as_of_date),
            )
        )
    return tuple(
        sorted(
            entries,
            key=lambda item: (
                item.last_performed_at,
                item.source_system.value,
                item.source_exercise_id,
            ),
            reverse=True,
        )[:result_limit]
    )


def _history_point(
    stored: StoredDetailedTrainingSession,
    source_exercise_id: str,
    policy: TrainingAnalyticsPolicy,
) -> ExerciseHistoryPoint | None:
    matching = tuple(
        analyze_exercise_occurrence(item, policy)
        for item in stored.session.exercises
        if item.source_exercise_id == source_exercise_id
    )
    if not matching:
        return None
    latest_name = max(matching, key=lambda item: item.exercise_order).display_name
    families = {item.metric_family for item in matching}
    family = next(iter(families)) if len(families) == 1 else ExerciseMetricFamily.OTHER
    rep_values = tuple(item.rep_total for item in matching if _is_rep_family(item.metric_family))
    volumes = tuple(item.volume_kg_reps for item in matching)
    top_sets = tuple(item.top_load_set for item in matching if item.top_load_set is not None)
    top = (
        max(top_sets, key=lambda item: (item.load_kg, item.reps, -item.set_index))
        if top_sets
        else None
    )
    max_rpe = max(
        (item.max_rpe for item in matching if item.max_rpe is not None),
        default=None,
    )
    complete = all(item.metric_completeness is MetricCompleteness.COMPLETE for item in matching)
    partially_available = any(
        item.metric_completeness is not MetricCompleteness.UNAVAILABLE for item in matching
    )
    return ExerciseHistoryPoint(
        revision_id=stored.revision_id,
        source_session_id=stored.session.source_session_id,
        source_revision=stored.session.source_revision,
        session_title=stored.session.title,
        started_at=stored.session.started_at,
        display_name=latest_name,
        occurrence_count=len(matching),
        metric_family=family,
        recorded_set_count=sum(item.recorded_set_count for item in matching),
        working_set_count=sum(item.working_set_count for item in matching),
        warmup_set_count=sum(item.warmup_set_count for item in matching),
        unsupported_set_count=sum(item.unsupported_set_count for item in matching),
        rep_total=(
            sum(value for value in rep_values if value is not None)
            if rep_values and all(value is not None for value in rep_values)
            else None
        ),
        max_load_kg=top.load_kg if top else None,
        top_load_set=top,
        volume_kg_reps=(
            sum((value for value in volumes if value is not None), Decimal(0))
            if volumes and all(value is not None for value in volumes)
            else None
        ),
        max_rpe=max_rpe,
        metric_completeness=(
            MetricCompleteness.COMPLETE
            if complete
            else MetricCompleteness.PARTIAL
            if partially_available
            else MetricCompleteness.UNAVAILABLE
        ),
    )


def _compare(
    latest: ExerciseHistoryPoint,
    previous: ExerciseHistoryPoint | None,
) -> ExerciseSessionComparison:
    if previous is None:
        return ExerciseSessionComparison(
            latest.revision_id,
            None,
            None,
            None,
            None,
            None,
            ("no_previous_session",),
        )
    latest_top = latest.top_load_set
    previous_top = previous.top_load_set
    reasons: list[str] = []
    top_delta = None
    reps_delta = None
    if latest_top is not None and previous_top is not None:
        top_delta = latest_top.load_kg - previous_top.load_kg
        if latest_top.load_kg == previous_top.load_kg:
            reps_delta = latest_top.reps - previous_top.reps
        else:
            reasons.append("top_loads_differ_for_rep_comparison")
    else:
        reasons.append("top_load_comparison_unavailable")
    volume_delta = None
    if latest.volume_kg_reps is not None and previous.volume_kg_reps is not None:
        volume_delta = latest.volume_kg_reps - previous.volume_kg_reps
    else:
        reasons.append("volume_comparison_unavailable")
    return ExerciseSessionComparison(
        latest_revision_id=latest.revision_id,
        previous_revision_id=previous.revision_id,
        working_set_count_delta=latest.working_set_count - previous.working_set_count,
        top_load_delta_kg=top_delta,
        reps_at_same_top_load_delta=reps_delta,
        volume_delta_kg_reps=volume_delta,
        reason_codes=tuple(reasons),
    )


def _pr_evidence(
    newest_first: tuple[ExerciseHistoryPoint, ...],
    source_system: DetailedTrainingSourceSystem,
    source_exercise_id: str,
    completeness: TrainingHistoryCompleteness,
    policy_version: str,
) -> tuple[TrainingPREvidence, ...]:
    events: list[TrainingPREvidence] = []
    highest_load: Decimal | None = None
    reps_by_load: dict[Decimal, int] = {}
    highest_volume: Decimal | None = None
    scope = (
        "within_synced_hevy_history"
        if completeness.source_bootstrap_complete and completeness.query_complete
        else "within_bounded_synced_hevy_history"
    )
    for point in reversed(newest_first):
        top = point.top_load_set
        if top is not None:
            if highest_load is None or top.load_kg > highest_load:
                highest_load = top.load_kg
                events.append(
                    _pr(
                        point,
                        TrainingPRType.HIGHEST_LOAD,
                        source_system,
                        source_exercise_id,
                        scope,
                        policy_version,
                        load=top.load_kg,
                        reps=top.reps,
                    )
                )
            prior_reps = reps_by_load.get(top.load_kg)
            if prior_reps is None or top.reps > prior_reps:
                reps_by_load[top.load_kg] = top.reps
                events.append(
                    _pr(
                        point,
                        TrainingPRType.MOST_REPS_AT_LOAD,
                        source_system,
                        source_exercise_id,
                        scope,
                        policy_version,
                        load=top.load_kg,
                        reps=top.reps,
                    )
                )
        if point.volume_kg_reps is not None and (
            highest_volume is None or point.volume_kg_reps > highest_volume
        ):
            highest_volume = point.volume_kg_reps
            events.append(
                _pr(
                    point,
                    TrainingPRType.HIGHEST_VOLUME_SESSION,
                    source_system,
                    source_exercise_id,
                    scope,
                    policy_version,
                    volume=point.volume_kg_reps,
                )
            )
    order = {
        TrainingPRType.HIGHEST_LOAD: 0,
        TrainingPRType.MOST_REPS_AT_LOAD: 1,
        TrainingPRType.HIGHEST_VOLUME_SESSION: 2,
    }
    return tuple(
        sorted(events, key=lambda item: (item.observed_at, -order[item.pr_type]), reverse=True)
    )


def _pr(
    point: ExerciseHistoryPoint,
    pr_type: TrainingPRType,
    source_system: DetailedTrainingSourceSystem,
    source_exercise_id: str,
    scope: str,
    policy_version: str,
    *,
    load: Decimal | None = None,
    reps: int | None = None,
    volume: Decimal | None = None,
) -> TrainingPREvidence:
    raw = "\x1f".join(
        (
            policy_version,
            pr_type.value,
            source_system.value,
            source_exercise_id,
            point.source_session_id,
            str(point.revision_id),
            str(load),
            str(reps),
            str(volume),
        )
    )
    return TrainingPREvidence(
        evidence_id=sha256(raw.encode("utf-8")).hexdigest(),
        pr_type=pr_type,
        revision_id=point.revision_id,
        source_session_id=point.source_session_id,
        observed_at=point.started_at,
        load_kg=load,
        reps=reps,
        volume_kg_reps=volume,
        scope=scope,
    )


def _frequency(
    points: tuple[ExerciseHistoryPoint, ...],
    timezone: str,
    zone: ZoneInfo,
    as_of_date: date,
) -> ExerciseFrequency:
    dates = tuple(
        point.started_at.astimezone(zone).date()
        for point in points
        if point.started_at.astimezone(zone).date() <= as_of_date
    )
    return ExerciseFrequency(
        timezone=timezone,
        as_of_date=as_of_date,
        sessions_last_7_days=sum((as_of_date - value).days < 7 for value in dates),
        sessions_last_28_days=sum((as_of_date - value).days < 28 for value in dates),
        days_since_last_performance=(as_of_date - max(dates)).days if dates else None,
    )


def _history_completeness(
    source_bootstrap_complete: bool,
    query_complete: bool,
) -> TrainingHistoryCompleteness:
    return TrainingHistoryCompleteness(
        source_bootstrap_complete=source_bootstrap_complete,
        query_complete=query_complete,
        lifetime_guaranteed=False,
        wording=(
            "PR within synced Hevy history; Hevy lifetime completeness is not guaranteed."
            if source_bootstrap_complete and query_complete
            else "PR within the bounded synced Hevy history returned; history is incomplete."
        ),
    )


def _metric_completeness(
    family: ExerciseMetricFamily,
    working: tuple[DetailedTrainingSet, ...],
    rep_total: int | None,
    volume: Decimal | None,
    unsupported: tuple[DetailedTrainingSet, ...],
) -> MetricCompleteness:
    if not working or family is ExerciseMetricFamily.OTHER:
        return MetricCompleteness.UNAVAILABLE
    if unsupported:
        return MetricCompleteness.PARTIAL
    if family is ExerciseMetricFamily.REP_LOAD:
        return MetricCompleteness.COMPLETE if volume is not None else MetricCompleteness.PARTIAL
    if family in {ExerciseMetricFamily.BODYWEIGHT_REP, ExerciseMetricFamily.ASSISTED_REP}:
        return MetricCompleteness.COMPLETE if rep_total is not None else MetricCompleteness.PARTIAL
    return MetricCompleteness.COMPLETE


def _rep_total(
    sets: tuple[DetailedTrainingSet, ...],
    family: ExerciseMetricFamily,
) -> int | None:
    if not _is_rep_family(family) or not sets or any(item.reps is None for item in sets):
        return None
    return sum(item.reps for item in sets if item.reps is not None)


def _volume(
    sets: tuple[DetailedTrainingSet, ...],
    family: ExerciseMetricFamily,
) -> Decimal | None:
    if family is not ExerciseMetricFamily.REP_LOAD or not sets:
        return None
    if any(not _valid_rep_load(item) for item in sets):
        return None
    return sum(
        (item.load.kilograms * item.reps for item in sets if item.load and item.reps is not None),
        Decimal(0),
    )


def _valid_rep_load(item: DetailedTrainingSet) -> bool:
    return (
        item.reps is not None
        and item.reps > 0
        and item.load is not None
        and item.load.kilograms > 0
    )


def _top_set(item: DetailedTrainingSet) -> TopLoadSetEvidence:
    assert item.reps is not None and item.load is not None
    return TopLoadSetEvidence(
        set_identity=item.set_identity,
        set_index=item.set_index,
        set_type=item.set_type,
        reps=item.reps,
        load_kg=item.load.kilograms,
        rpe=item.rpe,
    )


def _sum_present(values: Iterable[Decimal | None]) -> Decimal | None:
    materialized = tuple(value for value in values if value is not None)
    return sum(materialized, Decimal(0)) if materialized else None


def _is_rep_family(value: ExerciseMetricFamily) -> bool:
    return value in {
        ExerciseMetricFamily.REP_LOAD,
        ExerciseMetricFamily.BODYWEIGHT_REP,
        ExerciseMetricFamily.ASSISTED_REP,
    }


def _duration_decimal(session: DetailedTrainingSession) -> Decimal:
    delta = session.ended_at - session.started_at
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(
        1_000_000
    )


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTrainingAnalyticsTimezone("timezone is not recognized") from exc


__all__ = [
    "ExerciseCoachingGuidance",
    "ExerciseFrequency",
    "ExerciseHistoryPoint",
    "ExerciseIndexEntry",
    "ExerciseMetricFamily",
    "ExerciseOccurrenceAnalytics",
    "ExerciseSessionComparison",
    "ExerciseTrainingHistory",
    "InvalidTrainingAnalyticsTimezone",
    "MetricCompleteness",
    "OWNER_TRAINING_ANALYTICS_V1",
    "OWNER_TRAINING_COACHING_V1",
    "TopLoadSetEvidence",
    "TrainingAnalyticsPolicy",
    "TrainingCoachingAction",
    "TrainingCoachingPolicy",
    "TrainingCoachingStatus",
    "TrainingCoachingTarget",
    "TrainingHistoryCompleteness",
    "TrainingPREvidence",
    "TrainingPRType",
    "TrainingSessionAnalytics",
    "analyze_exercise_occurrence",
    "analyze_training_session",
    "build_exercise_history",
    "build_exercise_index",
    "classify_exercise_modality",
]
