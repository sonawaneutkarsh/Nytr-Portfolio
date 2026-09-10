"""Authenticated HTTP adapter for immutable plan-consumption events (M7 Step 7)."""

from __future__ import annotations

import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nutrition_agent.api.routes_planning import (
    VerifierDep,
    _authenticated_subject,
    _error,
)
from nutrition_agent.application.consumption import (
    ListConsumptionForRunUseCase,
    RecordConsumptionUseCase,
)
from nutrition_agent.application.ports import DuplicateConsumptionError, IdGenerator
from nutrition_agent.domain.consumption import ConsumptionEntry, ConsumptionState

logger = logging.getLogger("nutrition_agent.consumption_api")

router = APIRouter(prefix="/v1", tags=["consumption"])


class RecordConsumptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version_id: UUID
    item_id: UUID
    state: ConsumptionState
    client_event_id: UUID | None = None


def get_record_consumption_use_case(request: Request) -> RecordConsumptionUseCase:
    use_case: RecordConsumptionUseCase | None = getattr(
        request.app.state, "planning_record_consumption_use_case", None
    )
    if use_case is None:
        raise LookupError("consumption recording not configured")
    return use_case


def get_list_consumption_use_case(request: Request) -> ListConsumptionForRunUseCase:
    use_case: ListConsumptionForRunUseCase | None = getattr(
        request.app.state, "planning_list_consumption_use_case", None
    )
    if use_case is None:
        raise LookupError("consumption listing not configured")
    return use_case


def get_consumption_id_generator(request: Request) -> IdGenerator:
    ids: IdGenerator | None = getattr(request.app.state, "planning_id_generator", None)
    if ids is None:
        raise LookupError("planning id generator not configured")
    return ids


RecordDep = Annotated[RecordConsumptionUseCase, Depends(get_record_consumption_use_case)]
ListDep = Annotated[ListConsumptionForRunUseCase, Depends(get_list_consumption_use_case)]
IdsDep = Annotated[IdGenerator, Depends(get_consumption_id_generator)]


def _entry_content(entry: ConsumptionEntry) -> dict[str, str]:
    return {
        "entry_id": str(entry.entry_id),
        "plan_run_id": str(entry.plan_run_id),
        "plan_version_id": str(entry.plan_version_id),
        "item_id": str(entry.item_id),
        "state": entry.state.value,
        "recorded_at": entry.recorded_at.isoformat(),
        "client_event_id": str(entry.client_event_id),
    }


def _is_hidden_target_failure(exc: Exception) -> bool:
    """Recognize FK/RLS rejection without probing whether a foreign row exists."""
    return getattr(exc, "sqlstate", None) in {"23503", "42501"}


@router.post("/plans/{run_id}/consumption")
def record_consumption(
    run_id: UUID,
    payload: RecordConsumptionRequest,
    verifier: VerifierDep,
    record: RecordDep,
    ids: IdsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject

    try:
        client_event_id = payload.client_event_id or ids.new_id()
        outcome = record.execute(
            user_id=subject,
            plan_run_id=run_id,
            plan_version_id=payload.plan_version_id,
            item_id=payload.item_id,
            state=payload.state,
            client_event_id=client_event_id,
        )
    except DuplicateConsumptionError:
        logger.info(
            "consumption rejected: subject_hash=%x run=%s code=consumption_conflict",
            hash(subject) % (2**32),
            str(run_id)[:8],
        )
        return _error(
            409,
            "consumption_conflict",
            "the client event id was reused with conflicting consumption data",
        )
    except Exception as exc:  # noqa: BLE001 - adapter maps only safe public classes
        if _is_hidden_target_failure(exc):
            logger.info(
                "consumption rejected: subject_hash=%x run=%s code=target_not_found",
                hash(subject) % (2**32),
                str(run_id)[:8],
            )
            return _error(
                404,
                "consumption_target_not_found",
                "the plan consumption target was not found",
            )
        logger.error("consumption record failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "consumption recorded: subject_hash=%x run=%s state=%s created=%s duration_ms=%d",
        hash(subject) % (2**32),
        str(run_id)[:8],
        outcome.entry.state.value,
        outcome.created,
        duration_ms,
    )
    return JSONResponse(
        status_code=201 if outcome.created else 200,
        content=_entry_content(outcome.entry),
    )


@router.get("/plans/{run_id}/consumption")
def list_consumption(
    run_id: UUID,
    verifier: VerifierDep,
    listing: ListDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject

    try:
        entries = listing.execute(user_id=subject, plan_run_id=run_id)
    except Exception:  # noqa: BLE001 - fail closed without leaking repository details
        logger.error("consumption list failed: class=storage")
        return _error(503, "storage_unavailable", "retry later")

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "consumption listed: subject_hash=%x run=%s entries=%d duration_ms=%d",
        hash(subject) % (2**32),
        str(run_id)[:8],
        len(entries),
        duration_ms,
    )
    return JSONResponse(
        status_code=200,
        content={"entries": [_entry_content(entry) for entry in entries]},
    )


__all__ = ["router"]
