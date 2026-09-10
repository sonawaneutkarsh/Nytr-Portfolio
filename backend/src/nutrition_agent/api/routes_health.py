"""FastAPI routes for M5 body-mass sync and M9 derived trend retrieval.

Deliberately thin: parse → verify JWT → delegate to the use case → map
results to HTTP. No business logic here. Error contract (docs/APPLE_HEALTH.md):

- 200 accepted            400 domain validation rejection (fail-closed)
- 401 invalid/missing/expired token
- 413 batch size limit    422 malformed payload (FastAPI schema layer)
- 503 auth/DB not configured or repository failure (retryable)

Logging carries counts and error classes only — never weights, timestamps,
UUIDs of samples, or tokens.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.auth import AuthNotConfigured, InvalidToken, TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.health_sync import (
    HealthBodyMassSyncUseCase,
    Rejected,
)
from nutrition_agent.application.training_sync import (
    TrainingRejected,
    TrainingSessionSyncUseCase,
)
from nutrition_agent.domain.health.trend import (
    BodyMassTrendSummary,
    InvalidBodyMassTrendTimezone,
)

logger = logging.getLogger("nutrition_agent.health_api")

router = APIRouter(prefix="/v1/health", tags=["health"])

MAX_ADDED = 500
MAX_DELETED = 500


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "detail": detail}})


def get_settings(request: Request) -> HealthApiSettings:
    settings: HealthApiSettings = request.app.state.health_settings
    return settings


def get_verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.health_token_verifier
    return verifier


def get_use_case(request: Request) -> HealthBodyMassSyncUseCase:
    use_case: HealthBodyMassSyncUseCase = request.app.state.health_use_case
    return use_case


def get_trend_use_case(request: Request) -> BodyMassTrendUseCase:
    use_case: BodyMassTrendUseCase = request.app.state.health_trend_use_case
    return use_case


def get_training_use_case(request: Request) -> TrainingSessionSyncUseCase | None:
    use_case: TrainingSessionSyncUseCase | None = request.app.state.training_sync_use_case
    return use_case


SettingsDep = Annotated[HealthApiSettings, Depends(get_settings)]
VerifierDep = Annotated[TokenVerifier, Depends(get_verifier)]
UseCaseDep = Annotated[HealthBodyMassSyncUseCase, Depends(get_use_case)]
TrendUseCaseDep = Annotated[BodyMassTrendUseCase, Depends(get_trend_use_case)]
TrainingUseCaseDep = Annotated[TrainingSessionSyncUseCase | None, Depends(get_training_use_case)]


def _trend_content(summary: BodyMassTrendSummary) -> dict[str, object]:
    """Adapt the domain result without recalculating or rounding its values."""

    return {
        "status": summary.status.value,
        "as_of_date": summary.as_of_date.isoformat(),
        "timezone": summary.timezone,
        "algorithm_version": summary.algorithm_version,
        "input_digest": summary.input_digest,
        "represented_day_count": summary.represented_day_count,
        "coverage_span_days": summary.coverage_span_days,
        "first_measurement_date": (
            summary.first_measurement_date.isoformat()
            if summary.first_measurement_date is not None
            else None
        ),
        "last_measurement_date": (
            summary.last_measurement_date.isoformat()
            if summary.last_measurement_date is not None
            else None
        ),
        "latest_measurement_date": (
            summary.latest_measurement_date.isoformat()
            if summary.latest_measurement_date is not None
            else None
        ),
        "latest_measurement_age_days": summary.latest_measurement_age_days,
        "trailing_7d_average_kg": (
            str(summary.trailing_7d_average_kg)
            if summary.trailing_7d_average_kg is not None
            else None
        ),
        "weekly_rate_kg": (
            str(summary.weekly_rate_kg) if summary.weekly_rate_kg is not None else None
        ),
    }


@router.post("/body-mass/sync")
def submit_body_mass_sync(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: UseCaseDep,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    try:
        subject_str = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured as exc:
        logger.error("health sync rejected: auth not configured")
        return _error(503, "auth_unconfigured", f"{exc}")

    try:
        subject = UUID(subject_str)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")

    raw_batch_id = payload.get("client_batch_id")
    raw_added = payload.get("added")
    raw_deleted = payload.get("deleted")

    if isinstance(raw_added, list) and len(raw_added) > MAX_ADDED:
        return _error(413, "batch_too_large", "added exceeds limit")
    if isinstance(raw_deleted, list) and len(raw_deleted) > MAX_DELETED:
        return _error(413, "batch_too_large", "deleted exceeds limit")

    try:
        result = use_case.submit(subject, raw_batch_id, raw_added, raw_deleted)
    except Exception:  # noqa: BLE001 - repository failures are retryable
        logger.exception("health sync failed: repository error class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    if isinstance(result, Rejected):
        logger.info(
            "health sync rejected: subject_hash=%x errors=%d duration_ms=%d",
            hash(subject) % (2**32),
            len(result.errors),
            duration_ms,
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "batch_rejected",
                    "detail": [{"field": e.field, "reason": e.reason} for e in result.errors],
                }
            },
        )

    outcome = result.outcome
    logger.info(
        "health sync accepted: subject_hash=%x accepted=%d duplicates=%d"
        " deletions=%d dup_deletions=%d duration_ms=%d",
        hash(subject) % (2**32),
        outcome.accepted_added,
        outcome.duplicate_added,
        outcome.applied_deletions,
        outcome.duplicate_deletions,
        duration_ms,
    )
    status = use_case.status(subject)
    latest = status.latest_sample
    return JSONResponse(
        status_code=200,
        content={
            "accepted_added": outcome.accepted_added,
            "duplicate_added": outcome.duplicate_added,
            "applied_deletions": outcome.applied_deletions,
            "duplicate_deletions": outcome.duplicate_deletions,
            "ingested_at": (
                status.last_ingested_at.isoformat() if status.last_ingested_at is not None else None
            ),
            "latest_sample": (
                {
                    "sample_uuid": str(latest.sample_uuid),
                    "sample_start": latest.sample_start.isoformat(),
                    "value_kg": str(latest.value_kg),
                }
                if latest is not None
                else None
            ),
        },
    )


@router.get("/sync-status")
def get_sync_status(
    verifier: VerifierDep,
    use_case: UseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    try:
        subject_str = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured:
        logger.error("health status rejected: auth not configured")
        return _error(503, "auth_unconfigured", "auth configuration missing")

    try:
        subject = UUID(subject_str)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")

    try:
        status = use_case.status(subject)
    except Exception:  # noqa: BLE001 - storage failures are retryable
        logger.exception("health status failed: storage error class=storage")
        return _error(503, "storage_unavailable", "retry later")

    latest = status.latest_sample
    return JSONResponse(
        status_code=200,
        content={
            "record_count": status.record_count,
            "tombstone_count": status.tombstone_count,
            "latest_sample": (
                {
                    "sample_uuid": str(latest.sample_uuid),
                    "sample_start": latest.sample_start.isoformat(),
                    "value_kg": str(latest.value_kg),
                }
                if latest is not None
                else None
            ),
            "last_ingested_at": (
                status.last_ingested_at.isoformat() if status.last_ingested_at is not None else None
            ),
        },
    )


@router.post("/workouts/sync")
def submit_workout_sync(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: TrainingUseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Ingest one all-or-nothing HealthKit workout page for its JWT owner."""

    started = time.monotonic()
    try:
        subject_str = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured as exc:
        logger.error("workout sync rejected: auth not configured")
        return _error(503, "auth_unconfigured", f"{exc}")
    try:
        subject = UUID(subject_str)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")

    if use_case is None:
        return _error(503, "storage_unavailable", "workout storage not configured")
    raw_added = payload.get("added")
    raw_deleted = payload.get("deleted")
    if isinstance(raw_added, list) and len(raw_added) > MAX_ADDED:
        return _error(413, "batch_too_large", "added exceeds limit")
    if isinstance(raw_deleted, list) and len(raw_deleted) > MAX_DELETED:
        return _error(413, "batch_too_large", "deleted exceeds limit")

    try:
        result = use_case.submit(
            subject,
            payload.get("client_batch_id"),
            raw_added,
            raw_deleted,
        )
    except Exception:  # noqa: BLE001 - repository failures are retryable
        logger.exception("workout sync failed: repository error class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    if isinstance(result, TrainingRejected):
        logger.info(
            "workout sync rejected: subject_hash=%x errors=%d duration_ms=%d",
            hash(subject) % (2**32),
            len(result.errors),
            duration_ms,
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "batch_rejected",
                    "detail": [
                        {"field": error.field, "reason": error.reason} for error in result.errors
                    ],
                }
            },
        )

    outcome = result.outcome
    logger.info(
        "workout sync accepted: subject_hash=%x accepted=%d duplicates=%d"
        " deletions=%d dup_deletions=%d duration_ms=%d",
        hash(subject) % (2**32),
        outcome.accepted_added,
        outcome.duplicate_added,
        outcome.applied_deletions,
        outcome.duplicate_deletions,
        duration_ms,
    )
    return JSONResponse(
        status_code=200,
        content={
            "accepted_added": outcome.accepted_added,
            "duplicate_added": outcome.duplicate_added,
            "applied_deletions": outcome.applied_deletions,
            "duplicate_deletions": outcome.duplicate_deletions,
        },
    )


@router.get("/body-mass/trend")
def get_body_mass_trend(
    as_of_date: date,
    timezone: str,
    verifier: VerifierDep,
    use_case: TrendUseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Return one authenticated owner's derived body-mass trend; never write."""

    started = time.monotonic()
    try:
        subject_str = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured:
        logger.error("health trend rejected: auth not configured")
        return _error(503, "auth_unconfigured", "auth configuration missing")

    try:
        subject = UUID(subject_str)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")

    try:
        summary = use_case.execute(
            user_id=subject,
            as_of_date=as_of_date,
            timezone=timezone,
        )
    except InvalidBodyMassTrendTimezone:
        return _error(400, "invalid_request", "timezone is not recognized")
    except Exception:  # noqa: BLE001 - storage failures are retryable
        logger.exception("health trend failed: storage error class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "health trend returned: subject_hash=%x status=%s represented_days=%d duration_ms=%d",
        hash(subject) % (2**32),
        summary.status.value,
        summary.represented_day_count,
        duration_ms,
    )
    return JSONResponse(status_code=200, content=_trend_content(summary))
