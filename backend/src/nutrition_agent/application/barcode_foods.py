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
    ) -> ImportBarcodeFoodOutcome:
        product = self._provider.lookup(validate_barcode(barcode))
        if product.provenance.payload_sha256 != expected_payload_sha256:
            raise BarcodeProductChanged(
                "barcode product changed after review; scan and review it again"
            )
        provider = product.provenance.provider
        provider_code = product.provenance.provider_code
        if provider is None or provider_code is None:
            raise ValueError("barcode provider identity is incomplete")
        current = self._repository.find_latest_by_source(user_id, provider, provider_code)
        if current is not None and current.provenance.payload_sha256 == expected_payload_sha256:
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
            if (
                concurrent is not None
                and concurrent.provenance.payload_sha256 == expected_payload_sha256
            ):
                return ImportBarcodeFoodOutcome(concurrent, False)
            raise
        return ImportBarcodeFoodOutcome(version, True)


__all__ = [
    "ImportBarcodeFoodOutcome",
    "ImportBarcodeFoodUseCase",
    "LookupBarcodeFoodUseCase",
]
