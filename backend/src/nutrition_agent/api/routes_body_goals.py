"""Authenticated Body & Goals setup and progress endpoints."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.body_goals import BodyGoalsSnapshot, BodyGoalsUseCase
from nutrition_agent.domain.body_goals import OwnerBodyProfile, WaistMeasurement
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.target_review import GoalPolicyVersion

router = APIRouter(prefix="/v1/body-goals", tags=["body-goals"])


def get_use_case(request: Request) -> BodyGoalsUseCase:
    use_case: BodyGoalsUseCase | None = getattr(request.app.state, "body_goals_use_case", None)
    if use_case is None:
        raise LookupError("body-goals storage not configured")
    return use_case


UseCaseDep = Annotated[BodyGoalsUseCase, Depends(get_use_case)]


def _decimal(value: Any, name: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required as a decimal string")
    return Decimal(value)


def _profile_content(profile: OwnerBodyProfile | None) -> dict[str, object] | None:
    if profile is None:
        return None
    return {
        "height_cm": str(profile.height_cm),
        "target_weight_kg": str(profile.target_weight_kg)
        if profile.target_weight_kg is not None
        else None,
        "updated_at": profile.updated_at.isoformat(),
    }


def _waist_content(measurement: WaistMeasurement | None) -> dict[str, object] | None:
    if measurement is None:
        return None
    return {
        "waist_cm": str(measurement.waist_cm),
        "measured_at": measurement.measured_at.isoformat(),
        "recorded_at": measurement.recorded_at.isoformat(),
        "source": measurement.source,
    }


def _snapshot_content(snapshot: BodyGoalsSnapshot) -> dict[str, object]:
    progress = snapshot.progress
    return {
        "profile": _profile_content(snapshot.profile),
        "current_weight": {
            "value_kg": str(snapshot.latest_weight_kg)
            if snapshot.latest_weight_kg is not None
            else None,
            "measured_at": snapshot.latest_weight_at.isoformat()
            if snapshot.latest_weight_at
            else None,
            "freshness_days": snapshot.weight_freshness_days,
            "authority": "healthkit",
        },
        "latest_waist": _waist_content(snapshot.latest_waist),
        "waist_history": [_waist_content(item) for item in snapshot.waist_history],
        "goal": _goal_content(snapshot.goal),
        "target": _target_content(snapshot.target),
        "progress": (
            {
                "status": progress.status.value,
                "direction": progress.direction.value,
                "reason": progress.reason,
                "weight_rate_kg_per_week": str(progress.weight_rate_kg_per_week)
                if progress.weight_rate_kg_per_week is not None
                else None,
                "waist_rate_cm_per_week": str(progress.waist_rate_cm_per_week)
                if progress.waist_rate_cm_per_week is not None
                else None,
            }
            if progress is not None
            else None
        ),
    }


def _goal_content(goal: GoalPolicyVersion | None) -> dict[str, object] | None:
    if goal is None:
        return None
    return {
        "direction": goal.direction.value,
        "desired_rate_kg_per_week": str(goal.desired_rate_kg_per_week),
        "policy_version": goal.policy_version,
    }


def _target_content(target: TargetPolicyVersion | None) -> dict[str, object] | None:
    if target is None:
        return None
    calories = next(
        (item for item in target.goals_jsonb if item.get("nutrient") == "calories_kcal"), None
    )
    protein = next(
        (item for item in target.goals_jsonb if item.get("nutrient") == "protein_g"), None
    )
    return {
        "policy_version": target.policy_version,
        "calories_kcal": calories.get("value") if calories else None,
        "protein_g": protein.get("value") if protein else None,
        "approved_at": target.created_at.isoformat() if target.created_at else None,
    }


@router.get("")
def get_body_goals(
    verifier: VerifierDep,
    use_case: UseCaseDep,
    as_of_date: date,
    timezone: Annotated[str, Query(min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        snapshot = use_case.snapshot(user_id=subject, as_of_date=as_of_date, timezone=timezone)
    except ValueError as exc:
        return _error(400, "invalid_body_goals_query", str(exc))
    except Exception:
        return _error(503, "storage_unavailable", "body and goals are temporarily unavailable")
    return JSONResponse(status_code=200, content=_snapshot_content(snapshot))


@router.put("/profile")
def save_profile(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: UseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    if set(payload) - {"height_cm", "target_weight_kg"} or "height_cm" not in payload:
        return _error(
            422, "invalid_body_profile", "height_cm is required; target_weight_kg is optional"
        )
    try:
        target_raw = payload.get("target_weight_kg")
        profile = use_case.save_profile(
            user_id=subject,
            height_cm=_decimal(payload.get("height_cm"), "height_cm"),
            target_weight_kg=_decimal(target_raw, "target_weight_kg")
            if target_raw is not None
            else None,
        )
    except (ValueError, TypeError, InvalidOperation) as exc:
        return _error(422, "invalid_body_profile", str(exc))
    except Exception:
        return _error(503, "storage_unavailable", "body profile is temporarily unavailable")
    return JSONResponse(status_code=200, content=_profile_content(profile))


@router.post("/waist")
def record_waist(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: UseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    if set(payload) != {"value", "unit", "measured_at"}:
        return _error(422, "invalid_waist_measurement", "value, unit, and measured_at are required")
    try:
        value = _decimal(payload.get("value"), "value")
        unit = payload.get("unit")
        if unit == "in":
            value = value * Decimal("2.54")
        elif unit != "cm":
            raise ValueError("unit must be cm or in")
        measured_at = datetime.fromisoformat(str(payload["measured_at"]))
        measurement = use_case.record_waist(
            user_id=subject, waist_cm=value, measured_at=measured_at
        )
    except (ValueError, TypeError, InvalidOperation) as exc:
        return _error(422, "invalid_waist_measurement", str(exc))
    except Exception:
        return _error(503, "storage_unavailable", "waist evidence is temporarily unavailable")
    return JSONResponse(status_code=201, content=_waist_content(measurement))


@router.post("/starting-estimate")
def starting_estimate(
    payload: dict[str, Any],
    verifier: VerifierDep,
    use_case: UseCaseDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    if set(payload) != {"as_of_date", "timezone"}:
        return _error(422, "invalid_starting_estimate", "as_of_date and timezone are required")
    try:
        estimate = use_case.starting_estimate(
            user_id=subject,
            as_of_date=date.fromisoformat(str(payload["as_of_date"])),
            timezone=str(payload["timezone"]),
        )
    except (ValueError, TypeError, InvalidOperation) as exc:
        return _error(422, "invalid_starting_estimate", str(exc))
    except Exception:
        return _error(503, "storage_unavailable", "starting estimate is temporarily unavailable")
    return JSONResponse(
        status_code=200,
        content={
            "estimate_kcal": estimate.estimate_kcal,
            "weight_kg": str(estimate.weight_kg),
            "policy_version": estimate.policy_version,
            "confidence": estimate.confidence,
            "rationale": estimate.rationale,
            "approval_required": True,
        },
    )


__all__ = ["router"]
