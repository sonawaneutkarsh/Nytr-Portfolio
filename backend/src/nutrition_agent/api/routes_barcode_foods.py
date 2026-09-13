"""Authenticated exact-barcode lookup and reviewed immutable import API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from nutrition_agent.api.routes_manual_foods import _provenance_content, _version_content
from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.barcode_foods import (
    ImportBarcodeFoodUseCase,
    LookupBarcodeFoodUseCase,
)
from nutrition_agent.domain.nutrition.barcodes import (
    BarcodeProduct,
    BarcodeProductChanged,
    BarcodeProductIncomplete,
    BarcodeProductNotFound,
    BarcodeProviderUnavailable,
    BarcodeServingEvidenceRefused,
    OwnerServingEvidence,
)

router = APIRouter(prefix="/v1/nutrition/barcodes", tags=["barcode-foods"])


class OwnerServingRequest(BaseModel):
    """A physical serving the owner read off the package label."""

    model_config = ConfigDict(extra="forbid")
    amount: str
    unit: Literal["g", "ml"]
    label: Literal["serving", "bottle", "carton"] | None = None


class ImportBarcodeFoodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_payload_sha256: str
    owner_serving: OwnerServingRequest | None = None


def _owner_serving(payload: OwnerServingRequest | None) -> OwnerServingEvidence | None:
    if payload is None:
        return None
    try:
        amount = Decimal(payload.amount)
    except InvalidOperation as exc:
        raise ValueError("owner serving amount must be a decimal string") from exc
    return OwnerServingEvidence(amount=amount, unit=payload.unit, label=payload.label)


def _uses(request: Request) -> tuple[LookupBarcodeFoodUseCase, ImportBarcodeFoodUseCase]:
    lookup = getattr(request.app.state, "lookup_barcode_food_use_case", None)
    import_food = getattr(request.app.state, "import_barcode_food_use_case", None)
    if lookup is None or import_food is None:
        raise LookupError("barcode provider not configured")
    return lookup, import_food


def _product_content(value: BarcodeProduct) -> dict[str, object]:
    return {
        "policy_version": value.policy_version,
        "name": value.name,
        "brand": value.brand,
        "serving_description": value.serving_description,
        "serving_amount": value.serving_amount,
        "serving_unit": value.serving_unit,
        # Privacy-safe classification only: no source values, product identity,
        # or payload detail. Clients use it for diagnostics and to decide whether
        # an owner-entered serving can be offered.
        "basis_reason": value.basis_reason,
        "nutrition": {
            name: str(amount) if amount is not None else None
            for name, amount in value.nutrition.values().items()
        },
        "provenance": _provenance_content(value.provenance),
        "limitations": [
            "Community-contributed product data may be incomplete or inaccurate.",
            "Nytr does not infer or guess missing nutrition values.",
        ],
    }


def _lookup_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, BarcodeProductChanged):
        return _error(409, "barcode_product_changed", str(exc))
    if isinstance(exc, BarcodeServingEvidenceRefused):
        return _error(422, "owner_serving_unsupported", str(exc))
    if isinstance(exc, ValueError) and not isinstance(exc, BarcodeProductIncomplete):
        return _error(400, "invalid_barcode", str(exc))
    if isinstance(exc, BarcodeProductNotFound):
        return _error(404, "barcode_not_found", "product not found for this exact barcode")
    if isinstance(exc, BarcodeProductIncomplete):
        return _error(422, "barcode_nutrition_incomplete", str(exc))
    return _error(503, "barcode_provider_unavailable", "try again later")


@router.get("/{barcode}")
def lookup_barcode_food(
    barcode: str,
    verifier: VerifierDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        product = _uses(request)[0].execute(barcode=barcode)
    except (ValueError, BarcodeProductNotFound, BarcodeProviderUnavailable, LookupError) as exc:
        return _lookup_error(exc)
    return JSONResponse(status_code=200, content=_product_content(product))


@router.post("/{barcode}/import")
def import_barcode_food(
    barcode: str,
    payload: ImportBarcodeFoodRequest,
    verifier: VerifierDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        outcome = _uses(request)[1].execute(
            user_id=subject,
            barcode=barcode,
            expected_payload_sha256=payload.expected_payload_sha256,
            owner_serving=_owner_serving(payload.owner_serving),
        )
    except (
        ValueError,
        BarcodeProductChanged,
        BarcodeProductNotFound,
        BarcodeProviderUnavailable,
        LookupError,
    ) as exc:
        return _lookup_error(exc)
    return JSONResponse(
        status_code=201 if outcome.created else 200,
        content={"created": outcome.created, "food": _version_content(outcome.version)},
    )
