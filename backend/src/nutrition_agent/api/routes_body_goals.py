"""Authenticated Body & Goals API."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.body_goals import (
    AddWaistMeasurementUseCase,
    BodyGoalsConflict,
    BodyGoalsSummary,
    BodyGoalsUnavailable,
    CreateStartingCalorieProposalUseCase,
    DecideStartingCalorieProposalUseCase,
    GetBodyGoalsUseCase,
    SaveBodyGoalProfileUseCase,
)
from nutrition_agent.domain.body_goals import (
    ActivityLevel,
    BodyGoalProfileVersion,
    FormulaSex,
    StartingCalorieProposal,
    StartingTargetDecisionValue,
    WaistMeasurement,
    WaistUnit,
)

logger = logging.getLogger("nutrition_agent.body_goals_api")
router = APIRouter(prefix="/v1/body-goals", tags=["body-goals"])


class SummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of_date: date
    timezone: str


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    height_cm: Decimal
    date_of_birth: date
    formula_sex: FormulaSex
    activity_level: ActivityLevel
    target_weight_kg: Decimal | None = None


class WaistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Decimal
    unit: WaistUnit
    measured_at: datetime
    corrects_measurement_id: UUID | None = None


class StartingDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: StartingTargetDecisionValue
    client_event_id: UUID


def _dep(request: Request, name: str) -> object:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise LookupError("Body & Goals storage not configured")
    return value


def get_summary(request: Request) -> GetBodyGoalsUseCase:
    return _dep(request, "body_goals_get_use_case")  # type: ignore[return-value]


def get_profile(request: Request) -> SaveBodyGoalProfileUseCase:
    return _dep(request, "body_goals_save_profile_use_case")  # type: ignore[return-value]


def get_waist(request: Request) -> AddWaistMeasurementUseCase:
    return _dep(request, "body_goals_add_waist_use_case")  # type: ignore[return-value]


def get_proposal(request: Request) -> CreateStartingCalorieProposalUseCase:
    return _dep(request, "body_goals_create_proposal_use_case")  # type: ignore[return-value]


def get_decision(request: Request) -> DecideStartingCalorieProposalUseCase:
    return _dep(request, "body_goals_decide_proposal_use_case")  # type: ignore[return-value]


def _profile(value: BodyGoalProfileVersion | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "profile_id": str(value.profile_id),
        "policy_version": value.policy_version,
        "height_cm": str(value.height_cm),
        "date_of_birth": value.date_of_birth.isoformat(),
        "formula_sex": value.formula_sex.value,
        "activity_level": value.activity_level.value,
        "target_weight_kg": str(value.target_weight_kg)
        if value.target_weight_kg is not None
        else None,
        "provenance": "owner_entered",
        "created_at": value.created_at.isoformat(),
    }


def _waist(value: WaistMeasurement) -> dict[str, object]:
    return {
        "measurement_id": str(value.measurement_id),
        "measured_at": value.measured_at.isoformat(),
        "value_cm": str(value.value_cm),
        "entered_value": str(value.entered_value),
        "entered_unit": value.entered_unit.value,
        "provenance": value.provenance,
        "corrects_measurement_id": str(value.corrects_measurement_id)
        if value.corrects_measurement_id
        else None,
        "recorded_at": value.recorded_at.isoformat(),
    }


def _proposal(
    value: StartingCalorieProposal | None, decision: object = None
) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "proposal_id": str(value.proposal_id),
        "policy_version": value.policy_version,
        "as_of_date": value.as_of_date.isoformat(),
        "timezone": value.timezone,
        "age_years": value.age_years,
        "body_mass_kg": str(value.body_mass_kg),
        "bmr_kcal": str(value.bmr_kcal),
        "activity_multiplier": str(value.activity_multiplier),
        "maintenance_kcal": str(value.maintenance_kcal),
        "goal_adjustment_kcal": str(value.goal_adjustment_kcal),
        "proposed_calorie_kcal": str(value.proposed_calorie_kcal),
        "evidence_sha256": value.evidence_sha256,
        "created_at": value.created_at.isoformat(),
        "is_estimate": True,
        "requires_explicit_approval": decision is None,
        "decision_status": getattr(getattr(decision, "decision", None), "value", "pending"),
    }


def _summary(value: BodyGoalsSummary) -> dict[str, object]:
    target_goals = value.target.goals_jsonb if value.target else []
    calorie = next((g for g in target_goals if g["nutrient"] == "calories_kcal"), None)
    protein = next((g for g in target_goals if g["nutrient"] == "protein_g"), None)
    return {
        "as_of_date": value.as_of_date.isoformat(),
        "timezone": value.timezone,
        "weight": (
            {
                "value_kg": str(value.latest_weight.value_kg),
                "measured_at": value.latest_weight.sample_start.isoformat(),
                "age_days": value.weight_age_days,
                "authority": "healthkit",
            }
            if value.latest_weight
            else None
        ),
        "profile": _profile(value.profile),
        "waist": {
            "latest": _waist(value.waist_history[-1]) if value.waist_history else None,
            "history": [_waist(item) for item in reversed(value.waist_history[-12:])],
            "trend": {
                "policy_version": value.waist_trend.policy_version,
                "status": value.waist_trend.status.value,
                "latest_measurement_age_days": value.waist_trend.latest_measurement_age_days,
                "represented_day_count": value.waist_trend.represented_day_count,
                "coverage_span_days": value.waist_trend.coverage_span_days,
                "weekly_rate_cm": str(value.waist_trend.weekly_rate_cm)
                if value.waist_trend.weekly_rate_cm is not None
                else None,
            },
        },
        "goal": (
            {
                "direction": value.goal.direction.value,
                "desired_rate_kg_per_week": str(value.goal.desired_rate_kg_per_week),
            }
            if value.goal
            else None
        ),
        "approved_targets": {
            "calories_kcal": calorie["value"] if calorie else None,
            "protein_g": protein["value"] if protein else None,
        },
        "starting_calorie_proposal": _proposal(
            value.latest_starting_proposal, value.latest_starting_decision
        ),
        "phase_assessment": {
            "policy_version": value.phase_assessment.policy_version,
            "status": value.phase_assessment.status.value,
            "reason_codes": list(value.phase_assessment.reason_codes),
            "recommendation_only": True,
        },
    }


GetDep = Annotated[GetBodyGoalsUseCase, Depends(get_summary)]
ProfileDep = Annotated[SaveBodyGoalProfileUseCase, Depends(get_profile)]
WaistDep = Annotated[AddWaistMeasurementUseCase, Depends(get_waist)]
ProposalDep = Annotated[CreateStartingCalorieProposalUseCase, Depends(get_proposal)]
DecisionDep = Annotated[DecideStartingCalorieProposalUseCase, Depends(get_decision)]


@router.get("")
def read_body_goals(
    as_of_date: date,
    timezone: str,
    verifier: VerifierDep,
    use_case: GetDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        return JSONResponse(
            content=_summary(
                use_case.execute(user_id=subject, as_of_date=as_of_date, timezone=timezone)
            )
        )
    except (ValueError, BodyGoalsUnavailable) as exc:
        return _error(422, "body_goals_unavailable", str(exc))
    except Exception:
        logger.exception("body goals read failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")


@router.post("/profile")
def save_profile(
    payload: ProfileRequest,
    verifier: VerifierDep,
    use_case: ProfileDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        value = use_case.execute(user_id=subject, **payload.model_dump())
        return JSONResponse(status_code=201, content=_profile(value))
    except ValueError as exc:
        return _error(422, "invalid_body_goal_profile", str(exc))
    except Exception:
        logger.exception("body goal profile save failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")


@router.post("/waist")
def add_waist(
    payload: WaistRequest,
    verifier: VerifierDep,
    use_case: WaistDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        return JSONResponse(
            status_code=201,
            content=_waist(use_case.execute(user_id=subject, **payload.model_dump())),
        )
    except (ValueError, BodyGoalsConflict) as exc:
        return _error(422, "invalid_waist_measurement", str(exc))
    except Exception:
        logger.exception("waist save failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")


@router.post("/starting-target/proposals")
def create_proposal(
    payload: SummaryRequest,
    verifier: VerifierDep,
    use_case: ProposalDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        value, created = use_case.execute(user_id=subject, **payload.model_dump())
        content = _proposal(value)
        assert content is not None
        content["created"] = created
        return JSONResponse(status_code=201 if created else 200, content=content)
    except BodyGoalsUnavailable as exc:
        return _error(409, "starting_target_unavailable", str(exc))
    except ValueError as exc:
        return _error(422, "invalid_starting_target_request", str(exc))
    except Exception:
        logger.exception("starting target proposal failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")


@router.post("/starting-target/proposals/{proposal_id}/decision")
def decide_proposal(
    proposal_id: UUID,
    payload: StartingDecisionRequest,
    verifier: VerifierDep,
    use_case: DecisionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        value, created = use_case.execute(
            user_id=subject, proposal_id=proposal_id, **payload.model_dump()
        )
        return JSONResponse(
            status_code=201 if created else 200,
            content={
                "created": created,
                "decision_id": str(value.decision_id),
                "proposal_id": str(value.proposal_id),
                "decision": value.decision.value,
                "resulting_target_policy_version_id": str(value.resulting_target_policy_version_id)
                if value.resulting_target_policy_version_id
                else None,
                "decided_at": value.decided_at.isoformat(),
            },
        )
    except BodyGoalsUnavailable as exc:
        return _error(404, "starting_target_not_found", str(exc))
    except BodyGoalsConflict as exc:
        return _error(409, "starting_target_conflict", str(exc))
    except ValueError as exc:
        return _error(422, "invalid_starting_target_decision", str(exc))
    except Exception:
        logger.exception("starting target decision failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")


__all__ = ["router"]
