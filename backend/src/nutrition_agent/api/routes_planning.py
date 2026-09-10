"""FastAPI routes for M6 daily plans + target policies (ADR-017/018/019).

Thin: verify JWT (shared M5 TokenVerifier) → delegate to repositories → map to
HTTP with the M5 error envelope. NO_PLAN is a first-class 200 state, never an
error. Logging carries subject hash/state/counts only — never goal values,
plan payloads, or tokens.
"""

from __future__ import annotations

import logging
import time
from datetime import date as date_type
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.auth import AuthNotConfigured, InvalidToken, TokenVerifier
from nutrition_agent.application.ports import (
    PersistedPlanItemReference,
    PersistedPlanView,
    PlanRunRepository,
    TargetPolicyRepository,
)
from nutrition_agent.application.target_policy import (
    ApprovalOutcome,
    ApproveTargetPolicyUseCase,
    DuplicateTargetPolicyVersion,
    InvalidTargetPolicy,
)

logger = logging.getLogger("nutrition_agent.planning_api")

router = APIRouter(prefix="/v1", tags=["planning"])


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "detail": detail}})


def get_verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.health_token_verifier
    return verifier


def get_planning_runs(request: Request) -> PlanRunRepository:
    runs: PlanRunRepository | None = getattr(request.app.state, "planning_run_repository", None)
    if runs is None:
        raise LookupError("planning storage not configured")
    return runs


def get_planning_targets(request: Request) -> TargetPolicyRepository:
    targets: TargetPolicyRepository | None = getattr(
        request.app.state, "planning_target_repository", None
    )
    if targets is None:
        raise LookupError("planning storage not configured")
    return targets


def get_approval_use_case(request: Request) -> ApproveTargetPolicyUseCase:
    use_case: ApproveTargetPolicyUseCase | None = getattr(
        request.app.state, "planning_approval_use_case", None
    )
    if use_case is None:
        raise LookupError("planning storage not configured")
    return use_case


VerifierDep = Annotated[TokenVerifier, Depends(get_verifier)]
RunsDep = Annotated[PlanRunRepository, Depends(get_planning_runs)]
TargetsDep = Annotated[TargetPolicyRepository, Depends(get_planning_targets)]
ApprovalDep = Annotated[ApproveTargetPolicyUseCase, Depends(get_approval_use_case)]


def _plan_item_content(view: PersistedPlanView) -> list[dict[str, object]] | None:
    """Validate and expose DB identities without modifying the artifact."""
    if view.version_id is None or view.plan_jsonb is None:
        return None
    slots = view.plan_jsonb.get("slots")
    if not isinstance(slots, list):
        return None

    artifact_candidates: dict[tuple[int, int], str] = {}
    for slot_index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            return None
        candidates = slot.get("candidates")
        if not isinstance(candidates, list):
            return None
        for rank, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                return None
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, str):
                return None
            artifact_candidates[(slot_index, rank)] = candidate_id

    refs: dict[tuple[int, int], PersistedPlanItemReference] = {}
    for item in view.plan_items:
        key = (item.slot_index, item.rank)
        if item.version_id != view.version_id or key in refs:
            return None
        refs[key] = item
    if set(refs) != set(artifact_candidates):
        return None

    content: list[dict[str, object]] = []
    for key in sorted(refs):
        item = refs[key]
        if item.candidate_id != artifact_candidates[key]:
            return None
        content.append(
            {
                "item_id": str(item.item_id),
                "slot_index": item.slot_index,
                "rank": item.rank,
                "candidate_id": item.candidate_id,
            }
        )
    return content


def _authenticated_subject(verifier: VerifierDep, authorization: str | None) -> UUID | JSONResponse:
    try:
        subject_str = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured:
        logger.error("planning request rejected: auth not configured")
        return _error(503, "auth_unconfigured", "auth configuration missing")
    try:
        return UUID(subject_str)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")


@router.get("/plans/day")
def get_plan_for_day(
    verifier: VerifierDep,
    runs: RunsDep,
    targets: TargetsDep,
    requested_date: Annotated[date_type, Query(alias="date")],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject

    try:
        view: PersistedPlanView | None = runs.latest_for_user_date(subject, requested_date)
    except Exception:  # noqa: BLE001 - repository failures are retryable
        logger.exception("plan lookup failed: storage error class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    if view is None:
        logger.info(
            "plan day served: subject_hash=%x state=not_generated duration_ms=%d",
            hash(subject) % (2**32),
            duration_ms,
        )
        return JSONResponse(
            status_code=200,
            content={"state": "not_generated", "requested_date": requested_date.isoformat()},
        )

    # State authority is the RUN STATUS itself (ADR-019 §3): completed runs
    # expose their immutable artifact; no_plan runs surface the M4 reason
    # codes verbatim. Artifact presence alone cannot define state.
    if view.status == "no_plan":
        content: dict[str, Any] = {
            "state": "no_plan",
            "requested_date": view.requested_for_date.isoformat(),
            "reason_codes": list(view.reason_codes),
            "inputs_fingerprint": view.inputs_fingerprint,
        }
    elif (
        view.status == "completed"
        and view.plan_jsonb is not None
        and view.plan_canonical is not None
    ):
        plan_items = _plan_item_content(view)
        if plan_items is None:
            return _error(
                503,
                "storage_inconsistent",
                "plan item projection does not match the persisted artifact",
            )
        historical_policy: dict[str, object] | None = None
        if view.target_policy_version_id is not None:
            try:
                policy = targets.find_by_version_id(subject, view.target_policy_version_id)
            except Exception:  # noqa: BLE001 - repository failures are retryable
                logger.error("historical target policy lookup failed: class=storage")
                return _error(503, "storage_unavailable", "retry later")
            if policy is None:
                return _error(
                    503,
                    "storage_inconsistent",
                    "plan target policy reference cannot be resolved",
                )
            historical_policy = {
                "version_id": str(policy.version_id),
                "policy_version": policy.policy_version,
                "payload_sha256": policy.payload_sha256,
                "approved_at": policy.created_at.isoformat() if policy.created_at else None,
            }
        content = {
            "state": "completed",
            "requested_date": view.requested_for_date.isoformat(),
            "plan_date": view.requested_for_date.isoformat(),
            "plan_sha256": view.plan_sha256,
            "inputs_fingerprint": view.inputs_fingerprint,
            "run_id": str(view.run_id),
            "version_id": str(view.version_id),
            "plan": view.plan_jsonb,
            "plan_items": plan_items,
            "target_policy": historical_policy,
            "generated_at": view.started_at.isoformat(),
        }
    else:
        # Completed run missing its artifact would be a persistence bug;
        # fail closed rather than fabricate a payload.
        return _error(503, "storage_inconsistent", "completed run lacks its plan artifact")
    logger.info(
        "plan day served: subject_hash=%x state=%s sha=%s duration_ms=%d",
        hash(subject) % (2**32),
        content["state"],
        (view.plan_sha256 or "")[:12],
        duration_ms,
    )
    return JSONResponse(status_code=200, content=content)


@router.post("/target-policies")
def approve_target_policy(
    payload: dict[str, Any],
    verifier: VerifierDep,
    approval: ApprovalDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject

    label = payload.get("policy_version")
    rationale = payload.get("rationale")
    goals = payload.get("goals")
    if not isinstance(label, str):
        return _error(400, "invalid_target_policy", "policy_version must be a string")
    if not isinstance(rationale, str):
        return _error(400, "invalid_target_policy", "rationale must be a string")

    try:
        outcome: ApprovalOutcome = approval.execute(subject, label, goals, rationale)
    except InvalidTargetPolicy as exc:
        return _error(400, "invalid_target_policy", str(exc))
    except DuplicateTargetPolicyVersion:
        return _error(409, "duplicate_target_policy_version", "version already approved")
    except Exception:  # noqa: BLE001 - repository failures are retryable
        logger.exception("target policy failed: storage error class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "target policy approved: subject_hash=%x goals=%d duration_ms=%d",
        hash(subject) % (2**32),
        len(outcome.policy.goals_jsonb),
        duration_ms,
    )
    return JSONResponse(
        status_code=200,
        content={
            "version_id": str(outcome.policy.version_id),
            "policy_version": outcome.policy.policy_version,
            "payload_sha256": outcome.policy.payload_sha256,
            "approved_at": (
                outcome.policy.created_at.isoformat() if outcome.policy.created_at else None
            ),
        },
    )


@router.get("/target-policies/latest")
def get_latest_target_policy(
    verifier: VerifierDep,
    targets: TargetsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject

    try:
        policy = targets.latest_approved(subject)
    except Exception:  # noqa: BLE001 - fail closed without leaking repository details
        logger.error("target policy lookup failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    if policy is None:
        logger.info(
            "target policy served: subject_hash=%x state=none duration_ms=%d",
            hash(subject) % (2**32),
            duration_ms,
        )
        return JSONResponse(status_code=200, content=None)

    logger.info(
        "target policy served: subject_hash=%x state=approved goals=%d duration_ms=%d",
        hash(subject) % (2**32),
        len(policy.goals_jsonb),
        duration_ms,
    )
    return JSONResponse(
        status_code=200,
        content={
            "version_id": str(policy.version_id),
            "policy_version": policy.policy_version,
            "goals": [dict(goal) for goal in policy.goals_jsonb],
            "payload_sha256": policy.payload_sha256,
            "approved_at": policy.created_at.isoformat() if policy.created_at else None,
        },
    )


__all__ = ["router"]
