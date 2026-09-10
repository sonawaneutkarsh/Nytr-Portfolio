"""Authenticated M10 goal, review, and explicit terminal-decision API."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.auth import AuthNotConfigured, InvalidToken, TokenVerifier
from nutrition_agent.application.ports import GoalPolicyRepository
from nutrition_agent.application.target_review import (
    CreateGoalPolicyUseCase,
    CreateTargetReviewOutcome,
    CreateTargetReviewUseCase,
    DecideTargetReviewUseCase,
    DuplicateGoalPolicyVersion,
    GetLatestGoalPolicyUseCase,
    TargetReviewConflict,
    TargetReviewNotApprovable,
    TargetReviewNotFound,
    TargetReviewStale,
)
from nutrition_agent.domain.health.trend import InvalidBodyMassTrendTimezone
from nutrition_agent.domain.target_review import (
    DecideTargetReviewOutcome,
    GoalDirection,
    GoalPolicyVersion,
    TargetReview,
    TargetReviewDecisionValue,
)

logger = logging.getLogger("nutrition_agent.target_review_api")
router = APIRouter(prefix="/v1", tags=["target-review"])


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "detail": detail}})


def get_verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.health_token_verifier
    return verifier


def get_goal_repository(request: Request) -> GoalPolicyRepository:
    repository: GoalPolicyRepository | None = getattr(
        request.app.state, "target_review_goal_repository", None
    )
    if repository is None:
        raise LookupError("target-review storage not configured")
    return repository


def get_create_goal_use_case(request: Request) -> CreateGoalPolicyUseCase:
    use_case: CreateGoalPolicyUseCase | None = getattr(
        request.app.state, "target_review_create_goal_use_case", None
    )
    if use_case is None:
        raise LookupError("target-review storage not configured")
    return use_case


def get_create_review_use_case(request: Request) -> CreateTargetReviewUseCase:
    use_case: CreateTargetReviewUseCase | None = getattr(
        request.app.state, "target_review_create_use_case", None
    )
    if use_case is None:
        raise LookupError("target-review storage not configured")
    return use_case


def get_decide_review_use_case(request: Request) -> DecideTargetReviewUseCase:
    use_case: DecideTargetReviewUseCase | None = getattr(
        request.app.state, "target_review_decide_use_case", None
    )
    if use_case is None:
        raise LookupError("target-review storage not configured")
    return use_case


VerifierDep = Annotated[TokenVerifier, Depends(get_verifier)]
GoalsDep = Annotated[GoalPolicyRepository, Depends(get_goal_repository)]
CreateGoalDep = Annotated[CreateGoalPolicyUseCase, Depends(get_create_goal_use_case)]
CreateReviewDep = Annotated[CreateTargetReviewUseCase, Depends(get_create_review_use_case)]
DecideReviewDep = Annotated[DecideTargetReviewUseCase, Depends(get_decide_review_use_case)]


def _subject(verifier: TokenVerifier, authorization: str | None) -> UUID | JSONResponse:
    try:
        raw = verifier.verified_subject(authorization)
    except InvalidToken as exc:
        return _error(401, "invalid_token", str(exc))
    except AuthNotConfigured:
        return _error(503, "auth_unconfigured", "auth configuration missing")
    try:
        return UUID(raw)
    except ValueError:
        return _error(401, "invalid_token", "token sub is not a uuid")


def _goal_content(policy: GoalPolicyVersion) -> dict[str, object]:
    return {
        "version_id": str(policy.version_id),
        "policy_version": policy.policy_version,
        "direction": policy.direction.value,
        "desired_rate_kg_per_week": str(policy.desired_rate_kg_per_week),
        "payload_sha256": policy.payload_sha256,
        "created_at": policy.created_at.isoformat(),
    }


def _review_content(review: TargetReview, *, created: bool) -> dict[str, object]:
    evaluation = review.evaluation
    trend = evaluation.trend
    return {
        "state": "review",
        "created": created,
        "review_id": str(review.review_id),
        "created_at": review.created_at.isoformat(),
        "status": evaluation.status.value,
        "reason_codes": [reason.value for reason in evaluation.reason_codes],
        "as_of_date": evaluation.as_of_date.isoformat(),
        "timezone": trend.timezone,
        "trend": {
            "status": trend.status.value,
            "algorithm_version": trend.algorithm_version,
            "input_digest": trend.input_digest,
            "weekly_rate_kg": str(trend.weekly_rate_kg)
            if trend.weekly_rate_kg is not None
            else None,
        },
        "goal_policy": {
            **_goal_content(evaluation.goal_policy),
            "acceptable_rate_lower_kg_per_week": str(evaluation.acceptable_rate_lower),
            "acceptable_rate_upper_kg_per_week": str(evaluation.acceptable_rate_upper),
        },
        "target_policy": {
            "version_id": str(evaluation.prior_target_policy_version_id),
            "policy_version": evaluation.prior_target_policy_version,
            "payload_sha256": evaluation.prior_target_payload_sha256,
            "approved_at": evaluation.prior_target_approved_at.isoformat(),
            "current_calories_kcal": str(evaluation.current_calorie_target),
            "proposed_calories_kcal": (
                str(evaluation.proposed_calorie_target)
                if evaluation.proposed_calorie_target is not None
                else None
            ),
            "calorie_delta": (
                str(evaluation.calorie_delta) if evaluation.calorie_delta is not None else None
            ),
        },
        "review_policy": {
            "policy_version": evaluation.review_policy.policy_version,
            "deadband_kg_per_week": str(evaluation.review_policy.deadband_kg_per_week),
            "adjustment_step_kcal": str(evaluation.review_policy.adjustment_step_kcal),
            "cooldown_days": evaluation.review_policy.cooldown_days,
            "lower_calorie_bound": (
                str(evaluation.review_policy.lower_calorie_bound)
                if evaluation.review_policy.lower_calorie_bound is not None
                else None
            ),
            "upper_calorie_bound": (
                str(evaluation.review_policy.upper_calorie_bound)
                if evaluation.review_policy.upper_calorie_bound is not None
                else None
            ),
        },
        "recommendation_digest": evaluation.recommendation_digest,
    }


def _decision_content(outcome: DecideTargetReviewOutcome) -> dict[str, object]:
    decision = outcome.review_decision
    return {
        "created": outcome.created,
        "target_review_id": str(decision.review_id),
        "target_review_decision_id": str(decision.decision_id),
        "decision": decision.decision.value,
        "client_event_id": str(decision.client_event_id),
        "resulting_target_policy_id": (
            str(decision.resulting_target_policy_version_id)
            if decision.resulting_target_policy_version_id is not None
            else None
        ),
        "decided_at": decision.decided_at.isoformat(),
    }


@router.post("/goal-policies")
def create_goal_policy(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: CreateGoalDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    if set(payload) != {"policy_version", "direction", "desired_rate_kg_per_week"}:
        return _error(
            422,
            "invalid_goal_policy",
            "goal policy requests accept only policy_version, direction, "
            "and desired_rate_kg_per_week",
        )
    label = payload.get("policy_version")
    direction_raw = payload.get("direction")
    rate_raw = payload.get("desired_rate_kg_per_week")
    if (
        not isinstance(label, str)
        or not isinstance(direction_raw, str)
        or not isinstance(rate_raw, str)
    ):
        return _error(
            422, "invalid_goal_policy", "policy_version, direction, and rate are required strings"
        )
    try:
        direction = GoalDirection(direction_raw)
        rate = Decimal(rate_raw)
        policy = use_case.execute(
            user_id=subject,
            policy_version=label,
            direction=direction,
            desired_rate_kg_per_week=rate,
        )
    except (ValueError, TypeError, InvalidOperation) as exc:
        return _error(422, "invalid_goal_policy", str(exc))
    except DuplicateGoalPolicyVersion:
        return _error(409, "duplicate_goal_policy", "goal policy already exists")
    except Exception:
        logger.exception("goal policy creation failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(status_code=201, content=_goal_content(policy))


@router.get("/goal-policies/latest")
def get_latest_goal_policy(
    verifier: VerifierDep,
    goals: GoalsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        policy = GetLatestGoalPolicyUseCase(goals).execute(user_id=subject)
    except Exception:
        logger.exception("goal policy lookup failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(status_code=200, content=_goal_content(policy) if policy else None)


@router.post("/target-reviews")
def create_target_review(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: CreateReviewDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    if set(payload) != {"as_of_date", "timezone"}:
        return _error(
            422,
            "invalid_target_review",
            "review requests accept only as_of_date and timezone",
        )
    date_raw = payload.get("as_of_date")
    timezone = payload.get("timezone")
    if not isinstance(date_raw, str) or not isinstance(timezone, str) or not timezone:
        return _error(422, "invalid_target_review", "as_of_date and timezone are required strings")
    try:
        as_of_date = date.fromisoformat(date_raw)
    except ValueError:
        return _error(422, "invalid_target_review", "as_of_date must be YYYY-MM-DD")
    try:
        outcome: CreateTargetReviewOutcome = use_case.execute(
            user_id=subject,
            as_of_date=as_of_date,
            timezone=timezone,
        )
    except InvalidBodyMassTrendTimezone as exc:
        return _error(422, "invalid_timezone", str(exc))
    except (ValueError, TypeError) as exc:
        return _error(422, "invalid_target_review", str(exc))
    except Exception:
        logger.exception("target review creation failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")
    if outcome.review is None:
        assert outcome.unavailable_reason is not None
        return JSONResponse(
            status_code=200,
            content={"state": outcome.unavailable_reason.value},
        )
    return JSONResponse(
        status_code=201 if outcome.created else 200,
        content=_review_content(outcome.review, created=outcome.created),
    )


@router.post("/target-reviews/{review_id}/decision")
def decide_target_review(
    review_id: UUID,
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: DecideReviewDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    if set(payload) != {"decision", "idempotency_key"}:
        return _error(
            422,
            "invalid_target_review_decision",
            "decision requests accept only decision and idempotency_key",
        )
    decision_raw = payload.get("decision")
    event_raw = payload.get("idempotency_key")
    try:
        if not isinstance(decision_raw, str) or not isinstance(event_raw, str):
            raise ValueError("decision and idempotency_key are required strings")
        decision = TargetReviewDecisionValue(decision_raw)
        event_id = UUID(event_raw)
        outcome = use_case.execute(
            user_id=subject,
            review_id=review_id,
            decision=decision,
            client_event_id=event_id,
        )
    except TargetReviewNotFound:
        return _error(404, "target_review_not_found", "target review not found")
    except TargetReviewStale as exc:
        return _error(409, "target_review_stale", str(exc))
    except TargetReviewConflict as exc:
        return _error(409, "target_review_conflict", str(exc))
    except TargetReviewNotApprovable as exc:
        return _error(409, "target_review_not_approvable", str(exc))
    except (ValueError, TypeError) as exc:
        return _error(422, "invalid_target_review_decision", str(exc))
    except Exception:
        logger.exception("target review decision failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=201 if outcome.created else 200,
        content=_decision_content(outcome),
    )


__all__ = ["router"]
