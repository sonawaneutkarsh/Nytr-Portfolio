"""Authenticated read-only daily and seven-day nutrition APIs."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.routes_planning import (
    VerifierDep,
    _authenticated_subject,
    _error,
)
from nutrition_agent.application.daily_nutrition_ledger import (
    GetDailyNutritionLedgerUseCase,
    InvalidDailyNutritionLedgerRequest,
)
from nutrition_agent.application.nutrition_history import (
    GetNutritionHistory7DayUseCase,
    InvalidNutritionHistoryRequest,
)
from nutrition_agent.domain.nutrition.history import NutritionHistory7Day
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    DailyNutritionLedger,
)

logger = logging.getLogger("nutrition_agent.nutrition_api")

router = APIRouter(prefix="/v1/nutrition", tags=["nutrition"])


def get_daily_nutrition_ledger_use_case(request: Request) -> GetDailyNutritionLedgerUseCase:
    use_case: GetDailyNutritionLedgerUseCase | None = getattr(
        request.app.state, "daily_nutrition_ledger_use_case", None
    )
    if use_case is None:
        raise LookupError("daily nutrition ledger not configured")
    return use_case


LedgerDep = Annotated[
    GetDailyNutritionLedgerUseCase,
    Depends(get_daily_nutrition_ledger_use_case),
]


def get_nutrition_history_use_case(request: Request) -> GetNutritionHistory7DayUseCase:
    use_case: GetNutritionHistory7DayUseCase | None = getattr(
        request.app.state, "nutrition_history_use_case", None
    )
    if use_case is None:
        raise LookupError("nutrition history not configured")
    return use_case


HistoryDep = Annotated[
    GetNutritionHistory7DayUseCase,
    Depends(get_nutrition_history_use_case),
]


def _decimal(value: object) -> str | None:
    return str(value) if value is not None else None


def _item_content(item: ConsumedNutritionEvidence) -> dict[str, object]:
    return {
        "entry_id": str(item.entry_id),
        "recorded_at": item.recorded_at.isoformat(),
        "plan_run_id": str(item.plan_run_id) if item.plan_run_id is not None else None,
        "plan_version_id": str(item.plan_version_id) if item.plan_version_id is not None else None,
        "plan_item_id": str(item.plan_item_id) if item.plan_item_id is not None else None,
        "meal_context": item.meal_context,
        "candidate_id": item.candidate_id,
        "item_name": item.item_name,
        "configuration_summary": item.configuration_summary,
        "nutrition_authority": item.authority.value,
        "confidence": item.confidence,
        "calories_kcal": _decimal(item.calories_kcal),
        "protein_g": _decimal(item.protein_g),
        "unknown_nutrients": list(item.unknown_nutrients),
        "provenance_summary": item.provenance_summary,
        "source_system": item.source_system,
        "custom_food_id": str(item.custom_food_id) if item.custom_food_id is not None else None,
        "custom_food_version_id": (
            str(item.custom_food_version_id) if item.custom_food_version_id is not None else None
        ),
        "consumed_amount": _decimal(item.consumed_amount),
        "consumed_unit": item.consumed_unit,
        "serving_description": item.serving_description,
        "serving_amount": _decimal(item.serving_amount),
        "serving_unit": item.serving_unit,
    }


def _ledger_content(ledger: DailyNutritionLedger) -> dict[str, object]:
    target = ledger.target
    return {
        "local_date": ledger.local_date.isoformat(),
        "timezone": ledger.timezone,
        "target": (
            {
                "policy_version_id": str(target.policy_version_id),
                "policy_version": target.policy_version,
                "calories_kcal": _decimal(target.calories_kcal),
                "calories_goal_kind": target.calories_goal_kind,
                "protein_g": _decimal(target.protein_g),
                "protein_goal_kind": target.protein_goal_kind,
            }
            if target is not None
            else None
        ),
        "consumed_item_count": ledger.consumed_item_count,
        "known_calories_consumed": _decimal(ledger.known_calories_consumed),
        "known_protein_g_consumed": _decimal(ledger.known_protein_g_consumed),
        "remaining_known_calories": _decimal(ledger.remaining_known_calories),
        "remaining_known_protein_g": _decimal(ledger.remaining_known_protein_g),
        "nutrition_completeness": ledger.nutrition_completeness.value,
        "nutrition_authorities": [authority.value for authority in ledger.authorities],
        "unknown_nutrients": list(ledger.unknown_nutrients),
        "consumed_items": [_item_content(item) for item in ledger.consumed_items],
        "reason_codes": list(ledger.reason_codes),
    }


def _history_content(history: NutritionHistory7Day) -> dict[str, object]:
    days: list[dict[str, object]] = []
    for day in history.days:
        content = _ledger_content(day.ledger)
        content["consumed_event_count"] = content.pop("consumed_item_count")
        content.update(
            {
                "calorie_target": _decimal(day.calorie_target),
                "protein_target_g": _decimal(day.protein_target_g),
                "remaining_calories": _decimal(day.remaining_calories),
                "remaining_protein_g": _decimal(day.remaining_protein_g),
                "target_status": day.target_status.value,
                "calorie_adherence": day.calorie_adherence.value,
                "protein_adherence": day.protein_adherence.value,
                "reason_codes": list(day.reason_codes),
            }
        )
        days.append(content)
    summary = history.summary
    return {
        "start_date": history.start_date.isoformat(),
        "end_date": history.end_date.isoformat(),
        "timezone": history.timezone,
        "days": days,
        "summary": {
            "days_with_consumption": summary.days_with_consumption,
            "days_complete": summary.days_complete,
            "days_partial": summary.days_partial,
            "days_unavailable": summary.days_unavailable,
            "days_target_available": summary.days_target_available,
            "days_target_changed": summary.days_target_changed,
            "known_calories_total": _decimal(summary.known_calories_total),
            "known_protein_g_total": _decimal(summary.known_protein_g_total),
        },
    }


@router.get("/daily-ledger")
def get_daily_nutrition_ledger(
    verifier: VerifierDep,
    ledger_use_case: LedgerDep,
    local_date: Annotated[date, Query(alias="date")],
    timezone: Annotated[str, Query(min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        ledger = ledger_use_case.execute(
            user_id=subject,
            local_date=local_date,
            timezone=timezone,
        )
    except InvalidDailyNutritionLedgerRequest as exc:
        return _error(400, "invalid_daily_ledger_request", str(exc))
    except Exception:  # noqa: BLE001 - do not leak storage or artifact details
        logger.error("daily nutrition ledger failed: class=storage_or_artifact")
        return _error(503, "storage_unavailable", "retry later")

    logger.info(
        "daily nutrition ledger returned: subject_hash=%x items=%d completeness=%s duration_ms=%d",
        hash(subject) % (2**32),
        ledger.consumed_item_count,
        ledger.nutrition_completeness.value,
        int((time.monotonic() - started) * 1000),
    )
    return JSONResponse(status_code=200, content=_ledger_content(ledger))


@router.get("/history")
def get_nutrition_history(
    verifier: VerifierDep,
    history_use_case: HistoryDep,
    end_date: Annotated[date, Query()],
    timezone: Annotated[str, Query(min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        history = history_use_case.execute(
            user_id=subject,
            end_date=end_date,
            timezone=timezone,
        )
    except InvalidNutritionHistoryRequest as exc:
        return _error(400, "invalid_nutrition_history_request", str(exc))
    except Exception:  # noqa: BLE001 - do not leak storage or artifact details
        logger.error("nutrition history failed: class=storage_or_artifact")
        return _error(503, "storage_unavailable", "retry later")

    logger.info(
        "nutrition history returned: subject_hash=%x days=%d duration_ms=%d",
        hash(subject) % (2**32),
        len(history.days),
        int((time.monotonic() - started) * 1000),
    )
    return JSONResponse(status_code=200, content=_history_content(history))


__all__ = ["router"]
