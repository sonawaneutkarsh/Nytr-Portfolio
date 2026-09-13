"""Immutable owner-entered food definitions and factual consumption snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class CustomFoodAuthority(StrEnum):
    OWNER_ENTERED = "owner_entered"
    OPEN_FOOD_FACTS = "open_food_facts"


SERVING_AUTHORITY_OWNER_ENTERED = "owner_entered"


@dataclass(frozen=True)
class CustomFoodProvenance:
    authority: CustomFoodAuthority
    provider: str | None = None
    scanned_barcode: str | None = None
    provider_code: str | None = None
    product_url: str | None = None
    fetched_at: datetime | None = None
    payload_sha256: str | None = None
    data_license: str | None = None
    nutrition_basis: str | None = None
    # Records who established the physical serving basis when it did NOT come
    # from the provider. Only "owner_entered" is representable, so a serving the
    # owner supplied from the package label can never be mistaken for provider
    # evidence. The nutrition values themselves always keep `authority`.
    serving_authority: str | None = None

    def __post_init__(self) -> None:
        if self.serving_authority not in (None, SERVING_AUTHORITY_OWNER_ENTERED):
            raise ValueError("serving authority may only record an owner-entered serving")
        if self.authority is CustomFoodAuthority.OWNER_ENTERED:
            if any(
                value is not None
                for value in (
                    self.provider,
                    self.scanned_barcode,
                    self.provider_code,
                    self.product_url,
                    self.fetched_at,
                    self.payload_sha256,
                    self.data_license,
                    self.nutrition_basis,
                    # An owner-entered food's serving is already owner evidence,
                    # so the marker would be redundant and is not representable.
                    self.serving_authority,
                )
            ):
                raise ValueError("owner-entered provenance cannot claim provider evidence")
            return
        required = (
            self.provider,
            self.scanned_barcode,
            self.provider_code,
            self.product_url,
            self.fetched_at,
            self.payload_sha256,
            self.data_license,
            self.nutrition_basis,
        )
        if any(value is None for value in required):
            raise ValueError("provider provenance is incomplete")
        if (
            not self.scanned_barcode
            or not self.scanned_barcode.isascii()
            or not self.scanned_barcode.isdigit()
        ):
            raise ValueError("provider provenance requires a numeric scanned barcode")
        if (
            not self.provider_code
            or not self.provider_code.isascii()
            or not self.provider_code.isdigit()
        ):
            raise ValueError("provider provenance requires a numeric provider code")
        if self.fetched_at is not None:
            _require_aware(self.fetched_at, "fetched_at")
        if (
            self.payload_sha256 is None
            or len(self.payload_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.payload_sha256)
        ):
            raise ValueError("provider provenance requires a lowercase sha256")


OWNER_ENTERED_PROVENANCE = CustomFoodProvenance(CustomFoodAuthority.OWNER_ENTERED)


class ManualMealPeriod(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


@dataclass(frozen=True)
class ManualNutritionFacts:
    calories_kcal: Decimal | None = None
    protein_g: Decimal | None = None
    carbohydrate_g: Decimal | None = None
    total_fat_g: Decimal | None = None
    fiber_g: Decimal | None = None
    sodium_mg: Decimal | None = None

    def __post_init__(self) -> None:
        values = self.values()
        if not any(value is not None for value in values.values()):
            raise ValueError("at least one nutrition value is required")
        for name, value in values.items():
            if value is not None:
                if not isinstance(value, Decimal):
                    raise TypeError(f"{name} must be Decimal or None")
                if not value.is_finite() or value < 0:
                    raise ValueError(f"{name} must be finite and nonnegative")

    def values(self) -> dict[str, Decimal | None]:
        return {
            "calories_kcal": self.calories_kcal,
            "protein_g": self.protein_g,
            "carbohydrate_g": self.carbohydrate_g,
            "total_fat_g": self.total_fat_g,
            "fiber_g": self.fiber_g,
            "sodium_mg": self.sodium_mg,
        }

    def scaled(self, factor: Decimal) -> ManualNutritionFacts:
        if not isinstance(factor, Decimal) or not factor.is_finite() or factor <= 0:
            raise ValueError("portion factor must be a positive finite Decimal")
        return ManualNutritionFacts(
            **{
                name: value * factor if value is not None else None
                for name, value in self.values().items()
            }
        )


@dataclass(frozen=True)
class CustomFoodVersion:
    food_id: UUID
    version_id: UUID
    user_id: UUID
    name: str
    brand: str | None
    serving_description: str
    serving_amount: Decimal
    serving_unit: str
    nutrition: ManualNutritionFacts
    created_at: datetime
    provenance: CustomFoodProvenance = OWNER_ENTERED_PROVENANCE

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name is required")
        if self.brand is not None and not self.brand.strip():
            raise ValueError("brand must be non-empty when supplied")
        if not self.serving_description.strip() or not self.serving_unit.strip():
            raise ValueError("serving description and unit are required")
        if (
            not isinstance(self.serving_amount, Decimal)
            or not self.serving_amount.is_finite()
            or self.serving_amount <= 0
        ):
            raise ValueError("serving amount must be a positive finite Decimal")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True)
class ManualFoodConsumptionEntry:
    entry_id: UUID
    user_id: UUID
    food_id: UUID
    food_version_id: UUID
    client_event_id: UUID
    meal_period: ManualMealPeriod
    consumed_amount: Decimal
    consumed_unit: str
    portion_factor: Decimal
    food_name: str
    brand: str | None
    serving_description: str
    serving_amount: Decimal
    serving_unit: str
    nutrition: ManualNutritionFacts
    recorded_at: datetime
    source_system: str = "manual_custom"
    nutrition_authority: str = "user_entered"
    nutrition_confidence: str = "user_entered"
    provenance_summary: str = "Owner-entered nutrition frozen at consumption"

    def __post_init__(self) -> None:
        for name, value in (
            ("consumed_amount", self.consumed_amount),
            ("portion_factor", self.portion_factor),
            ("serving_amount", self.serving_amount),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive finite Decimal")
        if not self.consumed_unit.strip() or not self.food_name.strip():
            raise ValueError("consumed unit and food name are required")
        if not all(
            value.strip()
            for value in (
                self.source_system,
                self.nutrition_authority,
                self.nutrition_confidence,
                self.provenance_summary,
            )
        ):
            raise ValueError("manual consumption provenance fields are required")
        _require_aware(self.recorded_at, "recorded_at")

    def request_facts(self) -> tuple[object, ...]:
        """Logical request fields; excludes server-generated ID and timestamp."""
        return (
            self.user_id,
            self.food_id,
            self.food_version_id,
            self.client_event_id,
            self.meal_period,
            self.consumed_amount,
            self.consumed_unit,
            self.portion_factor,
            self.food_name,
            self.brand,
            self.serving_description,
            self.serving_amount,
            self.serving_unit,
            self.nutrition,
            self.source_system,
            self.nutrition_authority,
            self.nutrition_confidence,
            self.provenance_summary,
        )

    def correction_facts(self) -> tuple[object, ...]:
        """Replacement facts, excluding generated identity and persistence time."""
        return (
            self.user_id,
            self.food_id,
            self.food_version_id,
            self.meal_period,
            self.consumed_amount,
            self.consumed_unit,
            self.portion_factor,
            self.food_name,
            self.brand,
            self.serving_description,
            self.serving_amount,
            self.serving_unit,
            self.nutrition,
            self.source_system,
            self.nutrition_authority,
            self.nutrition_confidence,
            self.provenance_summary,
        )


@dataclass(frozen=True)
class RecordManualFoodOutcome:
    entry: ManualFoodConsumptionEntry
    created: bool


class ManualFoodAdjustmentKind(StrEnum):
    CORRECTION = "correction"
    VOID = "void"


@dataclass(frozen=True)
class ManualFoodConsumptionAdjustment:
    """Append-only replacement/void edge for one previously active event."""

    adjustment_id: UUID
    user_id: UUID
    client_event_id: UUID
    superseded_entry_id: UUID
    replacement_entry_id: UUID | None
    kind: ManualFoodAdjustmentKind
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.kind is ManualFoodAdjustmentKind.CORRECTION:
            if self.replacement_entry_id is None:
                raise ValueError("a correction requires a replacement entry")
            if self.replacement_entry_id == self.superseded_entry_id:
                raise ValueError("a correction must append a new entry")
        elif self.replacement_entry_id is not None:
            raise ValueError("a void cannot contain a replacement entry")
        _require_aware(self.recorded_at, "recorded_at")


@dataclass(frozen=True)
class AdjustManualFoodOutcome:
    adjustment: ManualFoodConsumptionAdjustment
    replacement: ManualFoodConsumptionEntry | None
    created: bool


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "SERVING_AUTHORITY_OWNER_ENTERED",
    "CustomFoodAuthority",
    "CustomFoodProvenance",
    "CustomFoodVersion",
    "ManualFoodConsumptionEntry",
    "ManualFoodConsumptionAdjustment",
    "ManualFoodAdjustmentKind",
    "ManualMealPeriod",
    "ManualNutritionFacts",
    "AdjustManualFoodOutcome",
    "RecordManualFoodOutcome",
    "OWNER_ENTERED_PROVENANCE",
]
