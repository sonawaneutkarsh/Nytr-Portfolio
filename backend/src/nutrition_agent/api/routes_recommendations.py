"""Authenticated M16A protein-proposal and next-meal endpoints."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.next_meal import (
    GenerateNextMealRecommendationUseCase,
    InvalidNextMealRequest,
    NextMealRequestConflict,
)
from nutrition_agent.application.next_meal_consumption import (
    GetNextMealConsumptionUseCase,
    NextMealConsumptionNotFound,
    NextMealConsumptionUnavailable,
    RecordNextMealConsumptionUseCase,
)
from nutrition_agent.application.ports import (
    DuplicateNextMealConsumptionError,
    NextMealRecommendationRepository,
    ProteinTargetProposalRepository,
)
from nutrition_agent.application.protein_target import (
    CreateProteinTargetProposalUseCase,
    DecideProteinTargetProposalUseCase,
    ProteinProposalConflict,
    ProteinProposalStale,
    ProteinProposalUnavailable,
)
from nutrition_agent.domain.next_meal import NextMealRecommendation
from nutrition_agent.domain.next_meal_consumption import NextMealConsumptionEntry
from nutrition_agent.domain.protein_target import (
    DecideProteinProposalOutcome,
    ProteinProposalDecisionValue,
    ProteinTargetProposal,
    ProteinTargetProposalDecision,
)

logger = logging.getLogger("nutrition_agent.recommendation_api")
router = APIRouter(prefix="/v1", tags=["recommendations"])


class NextMealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_date: date
    timezone: str
    client_request_id: UUID


class ProteinDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: ProteinProposalDecisionValue
    client_event_id: UUID
    rationale: str


class NextMealConsumptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_event_id: UUID


def _dep(request: Request, name: str) -> object:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise LookupError("M16A storage not configured")
    return value


def get_next_meal_use_case(request: Request) -> GenerateNextMealRecommendationUseCase:
    return _dep(request, "next_meal_generate_use_case")  # type: ignore[return-value]


def get_next_meal_repository(request: Request) -> NextMealRecommendationRepository:
    return _dep(request, "next_meal_repository")  # type: ignore[return-value]


def get_protein_create(request: Request) -> CreateProteinTargetProposalUseCase:
    return _dep(request, "protein_proposal_create_use_case")  # type: ignore[return-value]


def get_protein_decide(request: Request) -> DecideProteinTargetProposalUseCase:
    return _dep(request, "protein_proposal_decide_use_case")  # type: ignore[return-value]


def get_protein_repository(request: Request) -> ProteinTargetProposalRepository:
    return _dep(request, "protein_proposal_repository")  # type: ignore[return-value]


def get_next_meal_consumption_record(
    request: Request,
) -> RecordNextMealConsumptionUseCase:
    return _dep(request, "next_meal_consumption_record_use_case")  # type: ignore[return-value]


def get_next_meal_consumption_get(request: Request) -> GetNextMealConsumptionUseCase:
    return _dep(request, "next_meal_consumption_get_use_case")  # type: ignore[return-value]


NextMealDep = Annotated[GenerateNextMealRecommendationUseCase, Depends(get_next_meal_use_case)]
NextMealRepoDep = Annotated[NextMealRecommendationRepository, Depends(get_next_meal_repository)]
ProteinCreateDep = Annotated[CreateProteinTargetProposalUseCase, Depends(get_protein_create)]
ProteinDecideDep = Annotated[DecideProteinTargetProposalUseCase, Depends(get_protein_decide)]
ProteinRepoDep = Annotated[ProteinTargetProposalRepository, Depends(get_protein_repository)]
NextMealConsumptionRecordDep = Annotated[
    RecordNextMealConsumptionUseCase, Depends(get_next_meal_consumption_record)
]
NextMealConsumptionGetDep = Annotated[
    GetNextMealConsumptionUseCase, Depends(get_next_meal_consumption_get)
]


def _next_meal_content(value: NextMealRecommendation, *, created: bool | None) -> dict[str, object]:
    content: dict[str, object] = {
        "recommendation_id": str(value.recommendation_id),
        "client_request_id": str(value.client_request_id),
        "local_date": value.local_date.isoformat(),
        "timezone": value.timezone,
        "decision_at": value.decision_at.isoformat(),
        "status": value.status.value,
        "reason_codes": list(value.reason_codes),
        "inputs_digest": value.inputs_digest,
        "artifact_sha256": value.artifact_sha256,
        "artifact": value.artifact_jsonb,
    }
    if created is not None:
        content["created"] = created
    return content


def _next_meal_consumption_content(
    value: NextMealConsumptionEntry, *, created: bool | None
) -> dict[str, object]:
    content: dict[str, object] = {
        "entry_id": str(value.entry_id),
        "recommendation_id": str(value.recommendation_id),
        "client_event_id": str(value.client_event_id),
        "state": "eaten",
        "local_date": value.local_date.isoformat(),
        "timezone": value.timezone,
        "recorded_at": value.recorded_at.isoformat(),
        "recommendation_artifact_sha256": value.recommendation_artifact_sha256,
        "next_meal_policy_version": value.next_meal_policy_version,
        "meal_context": value.meal_context,
        "menu_period": value.menu_period,
        "candidate_id": value.candidate_id,
        "item_name": value.item_name,
        "serving_description": value.serving_description,
        "configuration_summary": value.configuration_summary,
        "nutrition_authority": value.nutrition_authority.value,
        "nutrition_confidence": value.nutrition_confidence,
        "calories_kcal": str(value.calories_kcal) if value.calories_kcal is not None else None,
        "protein_g": str(value.protein_g) if value.protein_g is not None else None,
        "unknown_nutrients": list(value.unknown_nutrients),
        "selected_candidate_sha256": value.selected_candidate_sha256,
    }
    if created is not None:
        content["created"] = created
    return content


def _proposal_content(
    value: ProteinTargetProposal,
    *,
    created: bool | None,
    decision: ProteinTargetProposalDecision | None,
) -> dict[str, object]:
    content: dict[str, object] = {
        "proposal_id": str(value.proposal_id),
        "prior_target_policy_version_id": str(value.prior_target_policy_version_id),
        "body_mass_sample_uuid": str(value.body_mass_sample_uuid),
        "policy_version": value.policy_version,
        "target_kind": value.target_kind.value,
        "body_mass_kg": str(value.body_mass_kg),
        "grams_per_pound": str(value.grams_per_pound),
        "proposed_protein_g": str(value.proposed_protein_g),
        "evidence_digest": value.evidence_digest,
        "calculation": value.calculation_payload,
        "rationale": value.rationale,
        "provenance": value.provenance,
        "generated_at": value.generated_at.isoformat(),
        "decision_status": decision.decision.value if decision is not None else "pending",
        "requires_explicit_approval": decision is None,
        "decision": (
            {
                "client_event_id": str(decision.client_event_id),
                "decided_at": decision.decided_at.isoformat(),
                "decision_id": str(decision.decision_id),
                "rationale": decision.rationale,
                "resulting_target_policy_version_id": (
                    str(decision.resulting_target_policy_version_id)
                    if decision.resulting_target_policy_version_id
                    else None
                ),
            }
            if decision is not None
            else None
        ),
    }
    if created is not None:
        content["created"] = created
    return content


def _decision_content(value: DecideProteinProposalOutcome) -> dict[str, object]:
    decision = value.decision
    return {
        "created": value.created,
        "decision_id": str(decision.decision_id),
        "proposal_id": str(decision.proposal_id),
        "decision": decision.decision.value,
        "client_event_id": str(decision.client_event_id),
        "resulting_target_policy_version_id": (
            str(decision.resulting_target_policy_version_id)
            if decision.resulting_target_policy_version_id
            else None
        ),
        "decided_at": decision.decided_at.isoformat(),
    }


@router.post("/recommendations/next-meal")
def generate_next_meal(
    payload: NextMealRequest,
    verifier: VerifierDep,
    use_case: NextMealDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        outcome = use_case.execute(
            user_id=subject,
            client_request_id=payload.client_request_id,
            local_date=payload.local_date,
            timezone=payload.timezone,
        )
    except InvalidNextMealRequest as exc:
        return _error(400, "invalid_next_meal_request", str(exc))
    except NextMealRequestConflict as exc:
        return _error(409, "next_meal_request_conflict", str(exc))
    except Exception:  # noqa: BLE001
        logger.error("next-meal generation failed: class=storage_or_evidence")
        return _error(503, "recommendation_unavailable", "retry later")
    return JSONResponse(
        status_code=201 if outcome.created else 200,
        content=_next_meal_content(outcome.recommendation, created=outcome.created),
    )


@router.get("/recommendations/next-meal/latest")
def latest_next_meal(
    verifier: VerifierDep,
    repository: NextMealRepoDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        value = repository.latest(subject)
    except Exception:  # noqa: BLE001
        return _error(503, "storage_unavailable", "retry later")
    if value is None:
        return _error(404, "next_meal_not_found", "generate a recommendation explicitly")
    return JSONResponse(content=_next_meal_content(value, created=None))


@router.post("/recommendations/next-meal/{recommendation_id}/consumption")
def record_next_meal_consumption(
    recommendation_id: UUID,
    payload: NextMealConsumptionRequest,
    verifier: VerifierDep,
    use_case: NextMealConsumptionRecordDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        outcome = use_case.execute(
            user_id=subject,
            recommendation_id=recommendation_id,
            client_event_id=payload.client_event_id,
        )
    except NextMealConsumptionNotFound:
        return _error(404, "next_meal_not_found", "next-meal recommendation not found")
    except NextMealConsumptionUnavailable as exc:
        return _error(409, "next_meal_not_consumable", str(exc))
    except DuplicateNextMealConsumptionError:
        return _error(
            409,
            "next_meal_consumption_conflict",
            "the recommendation or client event already has conflicting consumption",
        )
    except Exception:  # noqa: BLE001
        logger.error("next-meal consumption failed: class=storage_or_evidence")
        return _error(503, "consumption_unavailable", "retry later")
    return JSONResponse(
        status_code=201 if outcome.created else 200,
        content=_next_meal_consumption_content(outcome.entry, created=outcome.created),
    )


@router.get("/recommendations/next-meal/{recommendation_id}/consumption")
def get_next_meal_consumption(
    recommendation_id: UUID,
    verifier: VerifierDep,
    use_case: NextMealConsumptionGetDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        entry = use_case.execute(user_id=subject, recommendation_id=recommendation_id)
    except Exception:  # noqa: BLE001
        return _error(503, "storage_unavailable", "retry later")
    if entry is None:
        return _error(404, "next_meal_consumption_not_found", "not recorded as eaten")
    return JSONResponse(content=_next_meal_consumption_content(entry, created=None))


@router.post("/target-policies/protein-proposals")
def create_protein_proposal(
    verifier: VerifierDep,
    use_case: ProteinCreateDep,
    repository: ProteinRepoDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        proposal, created = use_case.execute(user_id=subject)
        decision = repository.find_decision(subject, proposal.proposal_id)
    except ProteinProposalUnavailable as exc:
        return _error(409, "protein_proposal_evidence_unavailable", str(exc))
    except Exception:  # noqa: BLE001
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=201 if created else 200,
        content=_proposal_content(proposal, created=created, decision=decision),
    )


@router.get("/target-policies/protein-proposals/latest")
def latest_protein_proposal(
    verifier: VerifierDep,
    repository: ProteinRepoDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        proposal = repository.latest(subject)
        decision = (
            repository.find_decision(subject, proposal.proposal_id)
            if proposal is not None
            else None
        )
    except Exception:  # noqa: BLE001
        return _error(503, "storage_unavailable", "retry later")
    if proposal is None:
        return _error(404, "protein_proposal_not_found", "generate a proposal explicitly")
    return JSONResponse(content=_proposal_content(proposal, created=None, decision=decision))


@router.post("/target-policies/protein-proposals/{proposal_id}/decision")
def decide_protein_proposal(
    proposal_id: UUID,
    payload: ProteinDecisionRequest,
    verifier: VerifierDep,
    use_case: ProteinDecideDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        outcome = use_case.execute(
            user_id=subject,
            proposal_id=proposal_id,
            decision=payload.decision,
            client_event_id=payload.client_event_id,
            rationale=payload.rationale,
        )
    except ProteinProposalUnavailable as exc:
        return _error(404, "protein_proposal_not_found", str(exc))
    except ProteinProposalStale as exc:
        return _error(409, "protein_proposal_stale", str(exc))
    except ProteinProposalConflict as exc:
        return _error(409, "protein_proposal_conflict", str(exc))
    except ValueError as exc:
        return _error(422, "invalid_protein_proposal_decision", str(exc))
    except Exception:  # noqa: BLE001
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=201 if outcome.created else 200, content=_decision_content(outcome)
    )
