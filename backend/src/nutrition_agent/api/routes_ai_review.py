"""Authenticated, side-effect-free M21 evidence-grounded review endpoint."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.ai_review import GenerateAIReviewUseCase
from nutrition_agent.domain.ai_review import AI_REVIEW_PROMPT_VERSION, AIReviewResult

logger = logging.getLogger("nutrition_agent.ai_review_api")
router = APIRouter(prefix="/v1/review", tags=["review"])


class GenerateAIReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    timezone: str = Field(min_length=1, max_length=255)


def get_ai_review_use_case(request: Request) -> GenerateAIReviewUseCase:
    use_case: GenerateAIReviewUseCase | None = getattr(
        request.app.state,
        "ai_review_use_case",
        None,
    )
    if use_case is None:
        raise LookupError("AI review is not configured")
    return use_case


AIReviewDep = Annotated[GenerateAIReviewUseCase, Depends(get_ai_review_use_case)]


@router.post("/current")
def generate_current_review(
    payload: GenerateAIReviewRequest,
    verifier: VerifierDep,
    use_case: AIReviewDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        result = use_case.execute(
            user_id=subject,
            as_of_date=payload.as_of_date,
            timezone=payload.timezone,
        )
    except ValueError as exc:
        return _error(400, "invalid_ai_review_request", str(exc))
    except Exception:  # noqa: BLE001 - never expose source health/storage details
        logger.error("AI review snapshot failed: class=storage_or_artifact")
        return _error(503, "storage_unavailable", "review evidence is temporarily unavailable")
    logger.info(
        "AI review completed: status=%s failure=%s",
        result.status.value,
        result.failure_code.value if result.failure_code is not None else "none",
    )
    return JSONResponse(status_code=200, content=_content(result))


@router.get("/snapshot")
def current_snapshot(
    as_of_date: date,
    timezone: str,
    verifier: VerifierDep,
    use_case: AIReviewDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Owner evidence only: there is no call path to a server AI provider."""
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        snapshot = use_case.snapshot(user_id=subject, as_of_date=as_of_date, timezone=timezone)
    except ValueError:
        return _error(400, "invalid_review_request", "Check the date and timezone")
    except Exception:
        return _error(503, "storage_unavailable", "Review evidence is temporarily unavailable")
    return JSONResponse(
        content={
            "snapshot": snapshot.client_document(),
            "model_input": snapshot.on_device_document(),
        },
        headers={"Cache-Control": "no-store"},
    )


def _content(result: AIReviewResult) -> dict[str, object]:
    review = result.review
    return {
        "status": result.status.value,
        "prompt_version": AI_REVIEW_PROMPT_VERSION,
        "snapshot": result.snapshot.client_document(),
        "review": (
            {
                "summary": review.summary,
                "attention_items": list(review.attention_items),
                "evidence_notes": list(review.evidence_notes),
                "limitations": list(review.limitations),
            }
            if review is not None
            else None
        ),
        "failure_code": result.failure_code.value if result.failure_code is not None else None,
        "authority_notice": (
            "AI explanation; Nytr's deterministic calculations remain authoritative."
        ),
    }


__all__ = ["router"]
