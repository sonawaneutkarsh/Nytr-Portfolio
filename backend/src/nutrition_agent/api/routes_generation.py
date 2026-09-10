"""Explicit synchronous daily-plan generation endpoint (M7 Step 6)."""

from __future__ import annotations

import logging
import time
from datetime import date as date_type
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from nutrition_agent.api.routes_planning import (
    TargetsDep,
    VerifierDep,
    _authenticated_subject,
    _error,
)
from nutrition_agent.application.daily_plan import DailyPlanOutcome, GenerateDailyPlanUseCase
from nutrition_agent.application.ports import (
    Clock,
    DuplicateLogicalPlanError,
    PersistedPlanView,
)
from nutrition_agent.application.server_inputs import (
    MenuDayUnavailableError,
    ServerInputsProvider,
)
from nutrition_agent.application.target_policy import build_domain_target_set
from nutrition_agent.domain.planning.artifacts import PlanRunStatus

logger = logging.getLogger("nutrition_agent.generation_api")

router = APIRouter(prefix="/v1", tags=["planning"])


class GeneratePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_date: date_type = Field(alias="date")
    timezone: str


def get_generation_use_case(request: Request) -> GenerateDailyPlanUseCase:
    use_case: GenerateDailyPlanUseCase | None = getattr(
        request.app.state, "planning_generation_use_case", None
    )
    if use_case is None:
        raise LookupError("planning generation not configured")
    return use_case


def get_server_inputs_provider(request: Request) -> ServerInputsProvider:
    provider: ServerInputsProvider | None = getattr(
        request.app.state, "planning_inputs_provider", None
    )
    if provider is None:
        raise LookupError("server inputs provider not configured")
    return provider


def get_generation_clock(request: Request) -> Clock:
    clock: Clock | None = getattr(request.app.state, "planning_clock", None)
    if clock is None:
        raise LookupError("planning clock not configured")
    return clock


GenerationDep = Annotated[GenerateDailyPlanUseCase, Depends(get_generation_use_case)]
InputsProviderDep = Annotated[ServerInputsProvider, Depends(get_server_inputs_provider)]
ClockDep = Annotated[Clock, Depends(get_generation_clock)]


def _completed_content(
    *,
    requested_date: date_type,
    run_id: object,
    version_id: object,
    plan_sha256: str | None,
    inputs_fingerprint: str,
    plan: dict[str, object] | None,
) -> dict[str, Any] | None:
    if version_id is None or plan_sha256 is None or plan is None:
        return None
    return {
        "state": "completed",
        "requested_date": requested_date.isoformat(),
        "plan_date": requested_date.isoformat(),
        "plan_sha256": plan_sha256,
        "inputs_fingerprint": inputs_fingerprint,
        "run_id": str(run_id),
        "version_id": str(version_id),
        "plan": plan,
    }


def _replay_content(view: PersistedPlanView) -> dict[str, Any] | None:
    if view.status == PlanRunStatus.NO_PLAN.value:
        return {
            "state": "no_plan",
            "requested_date": view.requested_for_date.isoformat(),
            "plan_date": view.requested_for_date.isoformat(),
            "reason_codes": list(view.reason_codes),
            "inputs_fingerprint": view.inputs_fingerprint,
            "run_id": str(view.run_id),
        }
    if view.status == PlanRunStatus.COMPLETED.value:
        return _completed_content(
            requested_date=view.requested_for_date,
            run_id=view.run_id,
            version_id=view.version_id,
            plan_sha256=view.plan_sha256,
            inputs_fingerprint=view.inputs_fingerprint,
            plan=view.plan_jsonb,
        )
    return None


def _new_content(outcome: DailyPlanOutcome) -> dict[str, Any] | None:
    run = outcome.run
    version = outcome.version
    if run is None:
        return None
    if run.status is PlanRunStatus.NO_PLAN:
        return {
            "state": "no_plan",
            "requested_date": run.requested_for_date.isoformat(),
            "plan_date": run.requested_for_date.isoformat(),
            "reason_codes": list(run.reason_codes),
            "inputs_fingerprint": run.inputs_fingerprint,
            "run_id": str(run.run_id),
        }
    if run.status is PlanRunStatus.COMPLETED and version is not None:
        return _completed_content(
            requested_date=run.requested_for_date,
            run_id=run.run_id,
            version_id=version.version_id,
            plan_sha256=version.plan_sha256,
            inputs_fingerprint=run.inputs_fingerprint,
            plan=version.plan_jsonb,
        )
    return None


@router.post("/plans/day/generate")
def generate_plan_for_day(
    payload: GeneratePlanRequest,
    verifier: VerifierDep,
    targets: TargetsDep,
    generation: GenerationDep,
    inputs_provider: InputsProviderDep,
    clock: ClockDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject

    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return _error(400, "invalid_request", "timezone is not recognized")

    try:
        target_policy = targets.latest_approved(subject)
    except Exception:  # noqa: BLE001 - fail closed without leaking storage details
        logger.error("plan generation failed: stage=target_policy class=storage")
        return _error(503, "storage_unavailable", "retry later")
    if target_policy is None:
        return _error(
            409,
            "no_approved_target_policy",
            "approve a target policy before generating a plan",
        )

    try:
        target_set = build_domain_target_set(target_policy.goals_jsonb)
    except Exception:  # noqa: BLE001 - persisted invalid policy is inconsistent storage
        logger.error("plan generation failed: stage=target_policy class=inconsistent")
        return _error(503, "storage_inconsistent", "approved target policy is invalid")

    try:
        inputs = inputs_provider.build(
            user_id=subject,
            requested_for_date=payload.requested_date,
            timezone=payload.timezone,
            targets=target_set,
            target_policy_version_id=target_policy.version_id,
        )
    except MenuDayUnavailableError:
        logger.info(
            "plan generation unavailable: subject_hash=%x date=%s class=menu",
            hash(subject) % (2**32),
            payload.requested_date.isoformat(),
        )
        return _error(503, "menu_data_unavailable", "retry later")
    except Exception:  # noqa: BLE001 - DB/provider errors are retryable
        logger.error("plan generation failed: stage=inputs class=storage")
        return _error(503, "storage_unavailable", "retry later")

    try:
        outcome = generation.execute(inputs, plan_at=clock.now())
    except DuplicateLogicalPlanError:
        logger.info(
            "plan generation conflict: subject_hash=%x date=%s code=concurrent_generation",
            hash(subject) % (2**32),
            payload.requested_date.isoformat(),
        )
        return _error(
            409,
            "concurrent_generation",
            "another generation request completed or is completing",
        )
    except Exception:  # noqa: BLE001 - fail closed without exposing repository details
        logger.error("plan generation failed: stage=execution class=storage")
        return _error(503, "storage_unavailable", "retry later")

    content = (
        _replay_content(outcome.replay_view)
        if outcome.replayed and outcome.replay_view is not None
        else _new_content(outcome)
    )
    if content is None:
        return _error(503, "storage_inconsistent", "generation outcome is incomplete")

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "plan generated: subject_hash=%x date=%s state=%s replay=%s fingerprint=%s sha=%s"
        " reasons=%d duration_ms=%d",
        hash(subject) % (2**32),
        payload.requested_date.isoformat(),
        content["state"],
        outcome.replayed,
        str(content["inputs_fingerprint"])[:12],
        str(content.get("plan_sha256") or "")[:12],
        len(content.get("reason_codes", [])),
        duration_ms,
    )
    return JSONResponse(status_code=200, content=content)


__all__ = ["router"]
