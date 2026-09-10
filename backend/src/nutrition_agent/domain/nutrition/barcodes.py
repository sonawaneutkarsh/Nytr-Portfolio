"""Exact barcode product facts; never search, interpolate, or estimate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodProvenance,
    ManualNutritionFacts,
)

BARCODE_IMPORT_POLICY_VERSION = "barcode-food-import.v1"


class BarcodeNutritionBasis(StrEnum):
    PER_SERVING = "per_serving"
    PER_100G = "per_100g"
    PER_100ML = "per_100ml"


@dataclass(frozen=True)
class BarcodeProduct:
    policy_version: str
    name: str
    brand: str | None
    serving_description: str
    serving_amount: str
    serving_unit: str
    nutrition: ManualNutritionFacts
    provenance: CustomFoodProvenance

    def __post_init__(self) -> None:
        if self.policy_version != BARCODE_IMPORT_POLICY_VERSION:
            raise ValueError("unsupported barcode import policy")
        if not self.name.strip() or not self.serving_description.strip():
            raise ValueError("barcode product identity is incomplete")
        if self.serving_unit not in {"serving", "g", "ml"}:
            raise ValueError("barcode product serving unit is unsupported")


class BarcodeProductNotFound(LookupError):
    pass


class BarcodeProductIncomplete(ValueError):
    pass


class BarcodeProviderUnavailable(RuntimeError):
    pass


class BarcodeProductChanged(RuntimeError):
    pass


def validate_barcode(value: str) -> str:
    barcode = value.strip()
    if not barcode.isascii() or not barcode.isdigit() or len(barcode) not in {8, 12, 13, 14}:
        raise ValueError("barcode must contain exactly 8, 12, 13, or 14 ASCII digits")
    return barcode


__all__ = [
    "BARCODE_IMPORT_POLICY_VERSION",
    "BarcodeNutritionBasis",
    "BarcodeProduct",
    "BarcodeProductIncomplete",
    "BarcodeProductChanged",
    "BarcodeProductNotFound",
    "BarcodeProviderUnavailable",
    "validate_barcode",
]
