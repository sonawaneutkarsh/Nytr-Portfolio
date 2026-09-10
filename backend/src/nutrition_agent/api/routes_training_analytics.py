"""Authenticated, read-only deterministic detailed-training analytics API."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.auth import AuthNotConfigured, InvalidToken, TokenVerifier
from nutrition_agent.application.training_analytics import (
    GetExerciseIndexUseCase,
    GetExerciseTrainingHistoryUseCase,
    GetRecentTrainingAnalyticsUseCase,
)
from nutrition_agent.domain.training.analytics import (
    ExerciseCoachingGuidance,
    ExerciseFrequency,
    ExerciseHistoryPoint,
    ExerciseOccurrenceAnalytics,
    ExerciseSessionComparison,
    TopLoadSetEvidence,
    TrainingHistoryCompleteness,
    TrainingPREvidence,
    TrainingSessionAnalytics,
)
from nutrition_agent.domain.training.detail import DetailedTrainingSourceSystem

router = APIRouter(prefix="/v1/training", tags=["training"])


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "detail": detail}})


def _verifier(request: Request) -> TokenVerifier:
    value: TokenVerifier = request.app.state.health_token_verifier
    return value


def _recent_use_case(request: Request) -> GetRecentTrainingAnalyticsUseCase | None:
    value: GetRecentTrainingAnalyticsUseCase | None = request.app.state.training_analytics_recent
    return value


def _index_use_case(request: Request) -> GetExerciseIndexUseCase | None:
    value: GetExerciseIndexUseCase | None = request.app.state.training_analytics_index
    return value


def _history_use_case(request: Request) -> GetExerciseTrainingHistoryUseCase | None:
    value: GetExerciseTrainingHistoryUseCase | None = request.app.state.training_analytics_history
    return value


VerifierDep = Annotated[TokenVerifier, Depends(_verifier)]
RecentDep = Annotated[GetRecentTrainingAnalyticsUseCase | None, Depends(_recent_use_case)]
IndexDep = Annotated[GetExerciseIndexUseCase | None, Depends(_index_use_case)]
HistoryDep = Annotated[GetExerciseTrainingHistoryUseCase | None, Depends(_history_use_case)]


def _subject(verifier: TokenVerifier, authorization: str | None) -> UUID | JSONResponse:
    try:
        raw_subject = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured:
        return _error(503, "auth_unconfigured", "auth configuration missing")
    try:
        return UUID(raw_subject)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")


@router.get("/analytics/recent")
def recent_training_analytics(
    verifier: VerifierDep,
    use_case: RecentDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    owner = _subject(verifier, authorization)
    if isinstance(owner, JSONResponse):
        return owner
    if use_case is None:
        return _error(503, "storage_unavailable", "training analytics storage unavailable")
    try:
        result = use_case.execute(user_id=owner, limit=limit)
    except Exception:  # noqa: BLE001 - database failures remain retryable adapter errors
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=200,
        content={
            "policy_version": result.policy_version,
            "sessions": [_session_content(item) for item in result.sessions],
        },
    )


@router.get("/exercises")
def exercise_index(
    verifier: VerifierDep,
    use_case: IndexDep,
    as_of_date: date,
    timezone: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    owner = _subject(verifier, authorization)
    if isinstance(owner, JSONResponse):
        return owner
    if use_case is None:
        return _error(503, "storage_unavailable", "training analytics storage unavailable")
    try:
        result = use_case.execute(
            user_id=owner,
            timezone=timezone,
            as_of_date=as_of_date,
            limit=limit,
        )
    except ValueError as exc:
        return _error(400, "invalid_training_query", str(exc))
    except Exception:  # noqa: BLE001 - database failures remain retryable adapter errors
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=200,
        content={
            "policy_version": result.policy_version,
            "completeness": _completeness_content(result.completeness),
            "exercises": [
                {
                    "source_system": item.source_system.value,
                    "source_exercise_id": item.source_exercise_id,
                    "latest_display_name": item.latest_display_name,
                    "last_performed_at": item.last_performed_at.isoformat(),
                    "session_count": item.session_count,
                    "latest_metric_family": item.latest_metric_family.value,
                    "latest_top_load_set": _top_set_content(item.latest_top_load_set),
                    "frequency": _frequency_content(item.frequency),
                }
                for item in result.exercises
            ],
        },
    )


@router.get("/exercises/{source_exercise_id}/history")
def exercise_history(
    source_exercise_id: str,
    verifier: VerifierDep,
    use_case: HistoryDep,
    as_of_date: date,
    timezone: Annotated[str, Query(min_length=1)],
    source_system: DetailedTrainingSourceSystem = DetailedTrainingSourceSystem.HEVY,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    owner = _subject(verifier, authorization)
    if isinstance(owner, JSONResponse):
        return owner
    if use_case is None:
        return _error(503, "storage_unavailable", "training analytics storage unavailable")
    try:
        result = use_case.execute(
            user_id=owner,
            source_system=source_system,
            source_exercise_id=source_exercise_id,
            timezone=timezone,
            as_of_date=as_of_date,
            limit=limit,
        )
    except ValueError as exc:
        return _error(400, "invalid_training_query", str(exc))
    except Exception:  # noqa: BLE001 - database failures remain retryable adapter errors
        return _error(503, "storage_unavailable", "retry later")
    if result is None:
        return _error(404, "exercise_history_not_found", "exercise history not found")
    return JSONResponse(
        status_code=200,
        content={
            "policy_version": result.policy_version,
            "source_system": result.source_system.value,
            "source_exercise_id": result.source_exercise_id,
            "latest_display_name": result.latest_display_name,
            "history": [_history_point_content(item) for item in result.history],
            "latest": _history_point_content(result.latest),
            "previous": (
                _history_point_content(result.previous) if result.previous is not None else None
            ),
            "comparison": _comparison_content(result.comparison),
            "frequency": _frequency_content(result.frequency),
            "pr_evidence": [_pr_content(item) for item in result.pr_evidence],
            "completeness": _completeness_content(result.completeness),
            "coaching": _coaching_content(result.coaching),
        },
    )


def _coaching_content(item: ExerciseCoachingGuidance) -> dict[str, object]:
    target = item.target
    return {
        "policy_version": item.policy_version,
        "analytics_policy_version": item.analytics_policy_version,
        "timezone": item.timezone,
        "as_of_date": item.as_of_date.isoformat(),
        "status": item.status.value,
        "action": item.action.value if item.action is not None else None,
        "metric_family": item.metric_family.value if item.metric_family is not None else None,
        "latest_revision_id": (
            str(item.latest_revision_id) if item.latest_revision_id is not None else None
        ),
        "previous_revision_id": (
            str(item.previous_revision_id) if item.previous_revision_id is not None else None
        ),
        "latest_started_at": (
            item.latest_started_at.isoformat() if item.latest_started_at is not None else None
        ),
        "previous_started_at": (
            item.previous_started_at.isoformat() if item.previous_started_at is not None else None
        ),
        "target": (
            {
                "working_set_count": target.working_set_count,
                "top_load_kg": _decimal(target.top_load_kg),
                "top_set_reps": target.top_set_reps,
                "total_reps": target.total_reps,
                "keep_assistance_constant": target.keep_assistance_constant,
            }
            if target is not None
            else None
        ),
        "reason_codes": list(item.reason_codes),
        "limitations": list(item.limitations),
    }


def _session_content(item: TrainingSessionAnalytics) -> dict[str, object]:
    return {
        "revision_id": str(item.revision_id),
        "source_system": item.source_system.value,
        "source_session_id": item.source_session_id,
        "source_revision": item.source_revision,
        "title": item.title,
        "started_at": item.started_at.isoformat(),
        "ended_at": item.ended_at.isoformat(),
        "session_duration_seconds": str(item.session_duration_seconds),
        "exercise_count": item.exercise_count,
        "recorded_set_count": item.recorded_set_count,
        "working_set_count": item.working_set_count,
        "warmup_set_count": item.warmup_set_count,
        "unsupported_set_count": item.unsupported_set_count,
        "rep_total": item.rep_total,
        "volume_kg_reps": _decimal(item.volume_kg_reps),
        "volume_exercise_count": item.volume_exercise_count,
        "exercises": [_occurrence_content(value) for value in item.exercises],
    }


def _occurrence_content(item: ExerciseOccurrenceAnalytics) -> dict[str, object]:
    return {
        "occurrence_identity": item.occurrence_identity,
        "source_exercise_id": item.source_exercise_id,
        "display_name": item.display_name,
        "exercise_order": item.exercise_order,
        "metric_family": item.metric_family.value,
        "recorded_set_count": item.recorded_set_count,
        "working_set_count": item.working_set_count,
        "warmup_set_count": item.warmup_set_count,
        "unsupported_set_count": item.unsupported_set_count,
        "rep_total": item.rep_total,
        "max_load_kg": _decimal(item.max_load_kg),
        "top_load_set": _top_set_content(item.top_load_set),
        "volume_kg_reps": _decimal(item.volume_kg_reps),
        "duration_seconds": _decimal(item.duration_seconds),
        "distance_meters": _decimal(item.distance_meters),
        "max_rpe": _decimal(item.max_rpe),
        "metric_completeness": item.metric_completeness.value,
        "reason_codes": list(item.reason_codes),
    }


def _history_point_content(item: ExerciseHistoryPoint) -> dict[str, object]:
    return {
        "revision_id": str(item.revision_id),
        "source_session_id": item.source_session_id,
        "source_revision": item.source_revision,
        "session_title": item.session_title,
        "started_at": item.started_at.isoformat(),
        "display_name": item.display_name,
        "occurrence_count": item.occurrence_count,
        "metric_family": item.metric_family.value,
        "recorded_set_count": item.recorded_set_count,
        "working_set_count": item.working_set_count,
        "warmup_set_count": item.warmup_set_count,
        "unsupported_set_count": item.unsupported_set_count,
        "rep_total": item.rep_total,
        "max_load_kg": _decimal(item.max_load_kg),
        "top_load_set": _top_set_content(item.top_load_set),
        "volume_kg_reps": _decimal(item.volume_kg_reps),
        "max_rpe": _decimal(item.max_rpe),
        "metric_completeness": item.metric_completeness.value,
    }


def _top_set_content(item: TopLoadSetEvidence | None) -> dict[str, object] | None:
    if item is None:
        return None
    return {
        "set_identity": item.set_identity,
        "set_index": item.set_index,
        "set_type": item.set_type.value,
        "reps": item.reps,
        "load_kg": str(item.load_kg),
        "rpe": _decimal(item.rpe),
    }


def _comparison_content(item: ExerciseSessionComparison) -> dict[str, object]:
    return {
        "latest_revision_id": str(item.latest_revision_id),
        "previous_revision_id": (
            str(item.previous_revision_id) if item.previous_revision_id else None
        ),
        "working_set_count_delta": item.working_set_count_delta,
        "top_load_delta_kg": _decimal(item.top_load_delta_kg),
        "reps_at_same_top_load_delta": item.reps_at_same_top_load_delta,
        "volume_delta_kg_reps": _decimal(item.volume_delta_kg_reps),
        "reason_codes": list(item.reason_codes),
    }


def _frequency_content(item: ExerciseFrequency) -> dict[str, object]:
    return {
        "timezone": item.timezone,
        "as_of_date": item.as_of_date.isoformat(),
        "sessions_last_7_days": item.sessions_last_7_days,
        "sessions_last_28_days": item.sessions_last_28_days,
        "days_since_last_performance": item.days_since_last_performance,
    }


def _pr_content(item: TrainingPREvidence) -> dict[str, object]:
    return {
        "evidence_id": item.evidence_id,
        "pr_type": item.pr_type.value,
        "revision_id": str(item.revision_id),
        "source_session_id": item.source_session_id,
        "observed_at": item.observed_at.isoformat(),
        "load_kg": _decimal(item.load_kg),
        "reps": item.reps,
        "volume_kg_reps": _decimal(item.volume_kg_reps),
        "scope": item.scope,
    }


def _completeness_content(item: TrainingHistoryCompleteness) -> dict[str, object]:
    return {
        "source_bootstrap_complete": item.source_bootstrap_complete,
        "query_complete": item.query_complete,
        "lifetime_guaranteed": item.lifetime_guaranteed,
        "wording": item.wording,
    }


def _decimal(value: object | None) -> str | None:
    return str(value) if value is not None else None


__all__ = ["router"]
