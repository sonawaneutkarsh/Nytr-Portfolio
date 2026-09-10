"""Authenticated read-only API for immutable detailed training revisions."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.auth import AuthNotConfigured, InvalidToken, TokenVerifier
from nutrition_agent.application.detailed_training import (
    GetDetailedTrainingSessionUseCase,
    ListDetailedTrainingSessionsUseCase,
)
from nutrition_agent.application.hevy_sync import SyncHevyDetailedTrainingUseCase
from nutrition_agent.application.ports import (
    DetailedTrainingSourceError,
    DetailedTrainingSourceFailureKind,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingSet,
    StoredDetailedTrainingSession,
)

router = APIRouter(prefix="/v1/training", tags=["training"])


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "detail": detail}})


def _verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.health_token_verifier
    return verifier


def _list_use_case(request: Request) -> ListDetailedTrainingSessionsUseCase | None:
    value: ListDetailedTrainingSessionsUseCase | None = (
        request.app.state.training_detail_list_use_case
    )
    return value


def _get_use_case(request: Request) -> GetDetailedTrainingSessionUseCase | None:
    value: GetDetailedTrainingSessionUseCase | None = request.app.state.training_detail_get_use_case
    return value


def _sync_use_case(request: Request) -> SyncHevyDetailedTrainingUseCase | None:
    value: SyncHevyDetailedTrainingUseCase | None = request.app.state.training_detail_sync_use_case
    return value


VerifierDep = Annotated[TokenVerifier, Depends(_verifier)]
ListUseCaseDep = Annotated[ListDetailedTrainingSessionsUseCase | None, Depends(_list_use_case)]
GetUseCaseDep = Annotated[GetDetailedTrainingSessionUseCase | None, Depends(_get_use_case)]
SyncUseCaseDep = Annotated[SyncHevyDetailedTrainingUseCase | None, Depends(_sync_use_case)]


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


@router.post("/hevy/sync")
def sync_hevy_training(
    verifier: VerifierDep,
    use_case: SyncUseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    owner = _subject(verifier, authorization)
    if isinstance(owner, JSONResponse):
        return owner
    if use_case is None:
        return _error(503, "storage_unavailable", "detailed training sync storage unavailable")
    try:
        outcome = use_case.execute(user_id=owner)
    except DetailedTrainingSourceError as exc:
        status, code = _source_error_contract(exc.kind)
        return _error(status, code, _source_error_detail(exc.kind))
    except Exception:  # noqa: BLE001 - source state must not advance on storage failure
        return _error(503, "storage_unavailable", "detailed training sync was not persisted")
    return JSONResponse(
        status_code=200,
        content={
            "status": outcome.status.value,
            "mode": outcome.mode.value,
            "sessions_created": outcome.sessions_created,
            "revisions_appended": outcome.revisions_appended,
            "deletions_recorded": outcome.deletions_recorded,
            "events_replayed": outcome.events_replayed,
            "tombstone_blocked": outcome.tombstone_blocked,
            "pages_fetched": outcome.pages_fetched,
            "has_more": outcome.has_more,
            "source_event_watermark": outcome.source_event_watermark.isoformat(),
            "logical_requests": outcome.logical_requests,
            "attempts_made": outcome.attempts_made,
            "retries": outcome.retries,
            "checkpoint_advanced": outcome.checkpoint_advanced,
        },
    )


def _source_error_contract(kind: DetailedTrainingSourceFailureKind) -> tuple[int, str]:
    if kind is DetailedTrainingSourceFailureKind.NOT_CONFIGURED:
        return 503, "hevy_not_configured"
    if kind is DetailedTrainingSourceFailureKind.UNAUTHORIZED:
        return 502, "hevy_unauthorized"
    if kind is DetailedTrainingSourceFailureKind.FORBIDDEN:
        return 502, "hevy_forbidden"
    if kind is DetailedTrainingSourceFailureKind.RATE_LIMITED:
        return 429, "hevy_rate_limited"
    if kind is DetailedTrainingSourceFailureKind.TIMEOUT:
        return 503, "hevy_timeout"
    if kind is DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE:
        return 503, "hevy_unavailable"
    if kind is DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION:
        return 502, "hevy_incomplete_pagination"
    return 502, "hevy_malformed_response"


def _source_error_detail(kind: DetailedTrainingSourceFailureKind) -> str:
    if kind is DetailedTrainingSourceFailureKind.NOT_CONFIGURED:
        return "Hevy integration is not configured"
    if kind in {
        DetailedTrainingSourceFailureKind.UNAUTHORIZED,
        DetailedTrainingSourceFailureKind.FORBIDDEN,
    }:
        return "Hevy access was rejected"
    if kind is DetailedTrainingSourceFailureKind.RATE_LIMITED:
        return "Hevy rate limited the bounded sync"
    if kind is DetailedTrainingSourceFailureKind.TIMEOUT:
        return "Hevy did not respond within the configured timeout"
    if kind is DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE:
        return "Hevy is temporarily unavailable"
    return "Hevy returned an incomplete or invalid source response"


@router.get("/sessions")
def list_training_sessions(
    verifier: VerifierDep,
    use_case: ListUseCaseDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    owner = _subject(verifier, authorization)
    if isinstance(owner, JSONResponse):
        return owner
    if use_case is None:
        return _error(503, "storage_unavailable", "detailed training storage unavailable")
    try:
        summaries = use_case.execute(user_id=owner, limit=limit)
    except Exception:  # noqa: BLE001 - database failures are retryable adapter errors
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=200,
        content={
            "sessions": [
                {
                    "revision_id": str(item.revision_id),
                    "source_system": item.source_system.value,
                    "source_session_id": item.source_session_id,
                    "source_revision": item.source_revision,
                    "title": item.title,
                    "started_at": item.started_at.isoformat(),
                    "ended_at": item.ended_at.isoformat(),
                    "exercise_count": item.exercise_count,
                    "set_count": item.set_count,
                }
                for item in summaries
            ]
        },
    )


@router.get("/sessions/{revision_id}")
def get_training_session(
    revision_id: UUID,
    verifier: VerifierDep,
    use_case: GetUseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    owner = _subject(verifier, authorization)
    if isinstance(owner, JSONResponse):
        return owner
    if use_case is None:
        return _error(503, "storage_unavailable", "detailed training storage unavailable")
    try:
        stored = use_case.execute(user_id=owner, revision_id=revision_id)
    except Exception:  # noqa: BLE001 - database failures are retryable adapter errors
        return _error(503, "storage_unavailable", "retry later")
    if stored is None:
        return _error(404, "training_session_not_found", "detailed training session not found")
    return JSONResponse(status_code=200, content=_detail_content(stored))


def _detail_content(stored: StoredDetailedTrainingSession) -> dict[str, object]:
    session = stored.session
    return {
        "revision_id": str(stored.revision_id),
        "source_system": session.source_system.value,
        "source_session_id": session.source_session_id,
        "source_revision": session.source_revision,
        "title": session.title,
        "description": session.description,
        "routine_id": session.routine_id,
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat(),
        "source_created_at": (
            session.source_created_at.isoformat() if session.source_created_at else None
        ),
        "source_updated_at": (
            session.source_updated_at.isoformat() if session.source_updated_at else None
        ),
        "parser_version": session.parser_version,
        "source_payload_sha256": session.source_payload_sha256,
        "ingested_at": stored.ingested_at.isoformat(),
        "exercises": [
            {
                "occurrence_identity": exercise.occurrence_identity,
                "source_exercise_id": exercise.source_exercise_id,
                "display_name": exercise.display_name,
                "exercise_order": exercise.exercise_order,
                "notes": exercise.notes,
                "superset_id": exercise.superset_id,
                "sets": [_set_content(item) for item in exercise.sets],
            }
            for exercise in session.exercises
        ],
    }


def _set_content(item: DetailedTrainingSet) -> dict[str, object]:
    return {
        "set_identity": item.set_identity,
        "source_set_id": item.source_set_id,
        "set_index": item.set_index,
        "set_type": item.set_type.value,
        "reps": item.reps,
        "load": (
            {"value": str(item.load.value), "unit": item.load.unit.value}
            if item.load is not None
            else None
        ),
        "distance": (
            {"value": str(item.distance.value), "unit": item.distance.unit.value}
            if item.distance is not None
            else None
        ),
        "duration_seconds": (
            str(item.duration_seconds) if item.duration_seconds is not None else None
        ),
        "rpe": str(item.rpe) if item.rpe is not None else None,
        "custom_metric": str(item.custom_metric) if item.custom_metric is not None else None,
    }


__all__ = ["router"]
