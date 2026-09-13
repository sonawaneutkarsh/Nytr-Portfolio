"""Application orchestration for exact barcode lookup and immutable import."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.manual_foods import CreateCustomFoodUseCase
from nutrition_agent.application.ports import (
    BarcodeProductProvider,
    CustomFoodRepository,
    DuplicateManualFoodError,
)
from nutrition_agent.domain.nutrition.barcodes import (
    BarcodeProduct,
    BarcodeProductChanged,
    OwnerServingEvidence,
    apply_owner_serving,
    validate_barcode,
)
from nutrition_agent.domain.nutrition.custom_foods import CustomFoodVersion


@dataclass(frozen=True)
class ImportBarcodeFoodOutcome:
    version: CustomFoodVersion
    created: bool


class LookupBarcodeFoodUseCase:
    def __init__(self, provider: BarcodeProductProvider) -> None:
        self._provider = provider

    def execute(self, *, barcode: str) -> BarcodeProduct:
        return self._provider.lookup(validate_barcode(barcode))


class ImportBarcodeFoodUseCase:
    def __init__(
        self,
        *,
        provider: BarcodeProductProvider,
        repository: CustomFoodRepository,
        create_food: CreateCustomFoodUseCase,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._create_food = create_food

    def execute(
        self,
        *,
        user_id: UUID,
        barcode: str,
        expected_payload_sha256: str,
        owner_serving: OwnerServingEvidence | None = None,
    ) -> ImportBarcodeFoodOutcome:
        product = self._provider.lookup(validate_barcode(barcode))
        if product.provenance.payload_sha256 != expected_payload_sha256:
            raise BarcodeProductChanged(
                "barcode product changed after review; scan and review it again"
            )
        # The owner serving is applied to the exact reviewed snapshot only. It
        # never alters the provider payload digest, so the source evidence the
        # owner reviewed stays byte-identical and independently verifiable.
        if owner_serving is not None:
            product = apply_owner_serving(product, owner_serving)
        provider = product.provenance.provider
        provider_code = product.provenance.provider_code
        if provider is None or provider_code is None:
            raise ValueError("barcode provider identity is incomplete")
        current = self._repository.find_latest_by_source(user_id, provider, provider_code)
        if current is not None and _already_current(current, product):
            return ImportBarcodeFoodOutcome(current, False)
        try:
            version = self._create_food.execute(
                user_id=user_id,
                food_id=current.food_id if current is not None else None,
                name=product.name,
                brand=product.brand,
                serving_description=product.serving_description,
                serving_amount=Decimal(product.serving_amount),
                serving_unit=product.serving_unit,
                nutrition=product.nutrition,
                provenance=product.provenance,
            )
        except DuplicateManualFoodError:
            concurrent = self._repository.find_latest_by_source(user_id, provider, provider_code)
            if concurrent is not None and _already_current(concurrent, product):
                return ImportBarcodeFoodOutcome(concurrent, False)
            raise
        return ImportBarcodeFoodOutcome(version, True)


def _already_current(version: CustomFoodVersion, product: BarcodeProduct) -> bool:
    """True when the stored latest version already states exactly this product.

    The provider digest alone is not sufficient: the same reviewed snapshot can
    be represented with a provider serving or with an owner-entered serving, and
    switching between them must append a new immutable version rather than
    silently resolve to the existing one.
    """

    return (
        version.provenance.payload_sha256 == product.provenance.payload_sha256
        and version.provenance.serving_authority == product.provenance.serving_authority
        and version.serving_unit == product.serving_unit
        and version.serving_description == product.serving_description
        and version.serving_amount == Decimal(product.serving_amount)
    )


__all__ = [
    "ImportBarcodeFoodOutcome",
    "ImportBarcodeFoodUseCase",
    "LookupBarcodeFoodUseCase",
]
