"""Exact barcode product facts; never search, interpolate, or estimate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from nutrition_agent.domain.nutrition.custom_foods import (
    SERVING_AUTHORITY_OWNER_ENTERED,
    CustomFoodProvenance,
    ManualNutritionFacts,
)

BARCODE_IMPORT_POLICY_VERSION = "barcode-food-import.v3"

MASS_BASIS_REFUSAL = (
    "This product's nutrition is mass-based, so Nytr cannot convert it to mL "
    "without a verified serving nutrition basis."
)
VOLUME_BASIS_REFUSAL = (
    "This product's nutrition is volume-based, so Nytr cannot convert it to g "
    "without a verified serving nutrition basis."
)

_PHYSICAL_UNITS = {"g", "ml"}
_SERVING_LABELS = {"serving", "bottle", "carton"}


class BarcodeNutritionBasis(StrEnum):
    PER_SERVING = "per_serving"
    PER_100G = "per_100g"
    PER_100ML = "per_100ml"


class BarcodeBasisReason(StrEnum):
    """Privacy-safe explanation of how the displayed serving basis was chosen."""

    STRUCTURED_SERVING_VOLUME = "structured_serving_volume"
    STRUCTURED_SERVING_MASS = "structured_serving_mass"
    SOURCE_SERVING_WITHOUT_PHYSICAL_QUANTITY = "source_serving_without_physical_quantity"
    EXPLICIT_PER_100ML = "explicit_per_100ml"
    NO_TRUSTWORTHY_VOLUME_EVIDENCE = "no_trustworthy_volume_evidence"
    OWNER_ENTERED_SERVING = "owner_entered_serving"


@dataclass(frozen=True)
class OwnerServingEvidence:
    """A physical serving the owner read off the package label.

    This is owner evidence, never provider authority. It can name or rescale a
    serving within one physical dimension; it can never establish a density.
    """

    amount: Decimal
    unit: str
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("owner serving amount must be a positive finite Decimal")
        if self.unit not in _PHYSICAL_UNITS:
            raise ValueError("owner serving unit must be g or ml")
        if self.label is not None and self.label not in _SERVING_LABELS:
            raise ValueError("owner serving label must be serving, bottle, or carton")


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
    basis_reason: str = BarcodeBasisReason.NO_TRUSTWORTHY_VOLUME_EVIDENCE.value

    def __post_init__(self) -> None:
        if self.policy_version != BARCODE_IMPORT_POLICY_VERSION:
            raise ValueError("unsupported barcode import policy")
        if not self.name.strip() or not self.serving_description.strip():
            raise ValueError("barcode product identity is incomplete")
        if self.serving_unit not in {"serving", "g", "ml"}:
            raise ValueError("barcode product serving unit is unsupported")
        if self.basis_reason not in {reason.value for reason in BarcodeBasisReason}:
            raise ValueError("barcode basis reason is unsupported")


class BarcodeProductNotFound(LookupError):
    pass


class BarcodeProductIncomplete(ValueError):
    pass


class BarcodeProviderUnavailable(RuntimeError):
    pass


class BarcodeProductChanged(RuntimeError):
    pass


class BarcodeServingEvidenceRefused(ValueError):
    """The owner serving cannot be reconciled with the source nutrition basis."""


def validate_barcode(value: str) -> str:
    barcode = value.strip()
    if not barcode.isascii() or not barcode.isdigit() or len(barcode) not in {8, 12, 13, 14}:
        raise ValueError("barcode must contain exactly 8, 12, 13, or 14 ASCII digits")
    return barcode


def display_unit(unit: str) -> str:
    return "mL" if unit == "ml" else unit


def apply_owner_serving(product: BarcodeProduct, evidence: OwnerServingEvidence) -> BarcodeProduct:
    """Represent a source product using a serving the owner read off the label.

    Nutrition values are only ever rescaled inside one physical dimension, or
    carried through unchanged when the source already attaches them to a
    serving. A volume the owner typed does NOT establish a density, so
    mass-based nutrition can never be re-expressed in mL and vice versa.
    """

    basis = product.provenance.nutrition_basis
    factor = _owner_serving_factor(basis, evidence.unit, evidence.amount)
    label = evidence.label or "serving"
    return replace(
        product,
        serving_description=(f"1 {label} ({evidence.amount} {display_unit(evidence.unit)})"),
        serving_amount=str(evidence.amount),
        serving_unit=evidence.unit,
        nutrition=(product.nutrition if factor == Decimal(1) else product.nutrition.scaled(factor)),
        provenance=replace(product.provenance, serving_authority=SERVING_AUTHORITY_OWNER_ENTERED),
        basis_reason=BarcodeBasisReason.OWNER_ENTERED_SERVING.value,
    )


def _owner_serving_factor(basis: str | None, unit: str, amount: Decimal) -> Decimal:
    if basis == BarcodeNutritionBasis.PER_SERVING.value:
        # Nutrition is already attached to one serving, so the owner is only
        # naming that serving's physical size. No arithmetic is required.
        return Decimal(1)
    if basis == BarcodeNutritionBasis.PER_100G.value:
        if unit != "g":
            raise BarcodeServingEvidenceRefused(MASS_BASIS_REFUSAL)
        return amount / Decimal(100)
    if basis == BarcodeNutritionBasis.PER_100ML.value:
        if unit != "ml":
            raise BarcodeServingEvidenceRefused(VOLUME_BASIS_REFUSAL)
        return amount / Decimal(100)
    raise BarcodeServingEvidenceRefused(
        "This product has no recorded nutrition basis, so Nytr cannot apply a serving to it."
    )


__all__ = [
    "BARCODE_IMPORT_POLICY_VERSION",
    "MASS_BASIS_REFUSAL",
    "VOLUME_BASIS_REFUSAL",
    "BarcodeBasisReason",
    "BarcodeNutritionBasis",
    "BarcodeProduct",
    "BarcodeProductIncomplete",
    "BarcodeProductChanged",
    "BarcodeProductNotFound",
    "BarcodeProviderUnavailable",
    "BarcodeServingEvidenceRefused",
    "OwnerServingEvidence",
    "apply_owner_serving",
    "display_unit",
    "validate_barcode",
]
