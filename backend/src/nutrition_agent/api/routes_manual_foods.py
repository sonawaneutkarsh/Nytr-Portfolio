"""Authenticated manual-food definitions and immutable consumption API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.manual_foods import (
    CreateCustomFoodUseCase,
    ListCustomFoodsUseCase,
    RecordManualFoodUseCase,
)
from nutrition_agent.application.ports import DuplicateManualFoodError
from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodVersion,
    ManualFoodConsumptionEntry,
    ManualMealPeriod,
    ManualNutritionFacts,
)

router = APIRouter(prefix="/v1/nutrition", tags=["manual-foods"])


class NutritionFactsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calories_kcal: str | None = None
    protein_g: str | None = None
    carbohydrate_g: str | None = None
    total_fat_g: str | None = None
    fiber_g: str | None = None
    sodium_mg: str | None = None


class CustomFoodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    brand: str | None = None
    serving_description: str
    serving_amount: str
    serving_unit: str
    nutrition: NutritionFactsRequest


class ManualConsumptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    food_id: UUID
    food_version_id: UUID
    consumed_amount: str
    consumed_unit: str
    meal_period: ManualMealPeriod
    client_event_id: UUID


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("nutrition and portion values must be decimal strings") from exc
    if not result.is_finite():
        raise ValueError("nutrition and portion values must be finite")
    return result


def _required_decimal(value: str) -> Decimal:
    result = _decimal(value)
    if result is None:
        raise ValueError("decimal value is required")
    return result


def _facts(payload: NutritionFactsRequest) -> ManualNutritionFacts:
    return ManualNutritionFacts(
        **{name: _decimal(value) for name, value in payload.model_dump().items()}
    )


def _version_content(value: CustomFoodVersion) -> dict[str, object]:
    n = value.nutrition
    return {
        "food_id": str(value.food_id),
        "version_id": str(value.version_id),
        "name": value.name,
        "brand": value.brand,
        "serving_description": value.serving_description,
        "serving_amount": str(value.serving_amount),
        "serving_unit": value.serving_unit,
        "nutrition": {
            name: str(amount) if amount is not None else None for name, amount in n.values().items()
        },
        "created_at": value.created_at.isoformat(),
        "provenance": _provenance_content(value.provenance),
    }


def _provenance_content(value: object) -> dict[str, object | None]:
    from nutrition_agent.domain.nutrition.custom_foods import CustomFoodProvenance

    if not isinstance(value, CustomFoodProvenance):
        raise TypeError("custom food provenance is invalid")
    return {
        "authority": value.authority.value,
        "provider": value.provider,
        "scanned_barcode": value.scanned_barcode,
        "provider_code": value.provider_code,
        "product_url": value.product_url,
        "fetched_at": value.fetched_at.isoformat() if value.fetched_at is not None else None,
        "payload_sha256": value.payload_sha256,
        "data_license": value.data_license,
        "nutrition_basis": value.nutrition_basis,
    }


def _entry_content(value: ManualFoodConsumptionEntry) -> dict[str, object]:
    return {
        "entry_id": str(value.entry_id),
        "food_id": str(value.food_id),
        "food_version_id": str(value.food_version_id),
        "client_event_id": str(value.client_event_id),
        "meal_period": value.meal_period.value,
        "consumed_amount": str(value.consumed_amount),
        "consumed_unit": value.consumed_unit,
        "portion_factor": str(value.portion_factor),
        "food_name": value.food_name,
        "recorded_at": value.recorded_at.isoformat(),
        "nutrition": {
            name: str(amount) if amount is not None else None
            for name, amount in value.nutrition.values().items()
        },
    }


def _uses(
    request: Request,
) -> tuple[CreateCustomFoodUseCase, ListCustomFoodsUseCase, RecordManualFoodUseCase]:
    values = (
        getattr(request.app.state, "create_custom_food_use_case", None),
        getattr(request.app.state, "list_custom_foods_use_case", None),
        getattr(request.app.state, "record_manual_food_use_case", None),
    )
    if any(value is None for value in values):
        raise LookupError("manual food storage not configured")
    return values  # type: ignore[return-value]


def _subject(verifier: VerifierDep, authorization: str | None) -> UUID | JSONResponse:
    return _authenticated_subject(verifier, authorization)


@router.get("/custom-foods")
def list_custom_foods(
    verifier: VerifierDep, request: Request, authorization: Annotated[str | None, Header()] = None
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        foods = _uses(request)[1].execute(user_id=subject)
    except Exception:
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(content={"foods": [_version_content(value) for value in foods]})


def _create(
    subject: UUID,
    payload: CustomFoodRequest,
    use_case: CreateCustomFoodUseCase,
    food_id: UUID | None,
) -> Response:
    try:
        value = use_case.execute(
            user_id=subject,
            food_id=food_id,
            name=payload.name,
            brand=payload.brand,
            serving_description=payload.serving_description,
            serving_amount=_required_decimal(payload.serving_amount),
            serving_unit=payload.serving_unit,
            nutrition=_facts(payload.nutrition),
        )
    except (ValueError, TypeError) as exc:
        return _error(400, "invalid_custom_food", str(exc))
    except LookupError:
        return _error(404, "custom_food_not_found", "custom food not found")
    except DuplicateManualFoodError:
        return _error(409, "custom_food_conflict", "custom food version conflicts")
    except Exception:
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(status_code=201, content=_version_content(value))


@router.post("/custom-foods")
def create_custom_food(
    payload: CustomFoodRequest,
    verifier: VerifierDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    return _create(subject, payload, _uses(request)[0], None)


@router.post("/custom-foods/{food_id}/versions")
def create_custom_food_version(
    food_id: UUID,
    payload: CustomFoodRequest,
    verifier: VerifierDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    return _create(subject, payload, _uses(request)[0], food_id)


@router.post("/manual-consumption")
def record_manual_consumption(
    payload: ManualConsumptionRequest,
    verifier: VerifierDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        outcome = _uses(request)[2].execute(
            user_id=subject,
            food_id=payload.food_id,
            food_version_id=payload.food_version_id,
            consumed_amount=_required_decimal(payload.consumed_amount),
            consumed_unit=payload.consumed_unit,
            meal_period=payload.meal_period,
            client_event_id=payload.client_event_id,
        )
    except (ValueError, TypeError) as exc:
        return _error(400, "invalid_manual_consumption", str(exc))
    except LookupError:
        return _error(404, "custom_food_not_found", "custom food version not found")
    except DuplicateManualFoodError:
        return _error(409, "manual_consumption_conflict", "client event conflicts")
    except Exception:
        return _error(503, "storage_unavailable", "retry later")
    return JSONResponse(
        status_code=201 if outcome.created else 200, content=_entry_content(outcome.entry)
    )
