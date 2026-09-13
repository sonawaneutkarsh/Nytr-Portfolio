"""Application orchestration for immutable owner-entered foods and intake."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.ports import Clock, CustomFoodRepository, IdGenerator
from nutrition_agent.domain.nutrition.custom_foods import (
    OWNER_ENTERED_PROVENANCE,
    AdjustManualFoodOutcome,
    CustomFoodProvenance,
    CustomFoodVersion,
    ManualFoodAdjustmentKind,
    ManualFoodConsumptionAdjustment,
    ManualFoodConsumptionEntry,
    ManualMealPeriod,
    ManualNutritionFacts,
    RecordManualFoodOutcome,
)


class CreateCustomFoodUseCase:
    def __init__(self, repository: CustomFoodRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        name: str,
        brand: str | None,
        serving_description: str,
        serving_amount: Decimal,
        serving_unit: str,
        nutrition: ManualNutritionFacts,
        food_id: UUID | None = None,
        provenance: CustomFoodProvenance = OWNER_ENTERED_PROVENANCE,
    ) -> CustomFoodVersion:
        now = _aware_now(self._clock)
        version = CustomFoodVersion(
            food_id=food_id or self._ids.new_id(),
            version_id=self._ids.new_id(),
            user_id=user_id,
            name=name.strip(),
            brand=brand.strip() if brand is not None else None,
            serving_description=serving_description.strip(),
            serving_amount=serving_amount,
            serving_unit=serving_unit.strip(),
            nutrition=nutrition,
            created_at=now,
            provenance=provenance,
        )
        self._repository.save_version(version, create_identity=food_id is None)
        return version


class ListCustomFoodsUseCase:
    def __init__(self, repository: CustomFoodRepository) -> None:
        self._repository = repository

    def execute(self, *, user_id: UUID) -> tuple[CustomFoodVersion, ...]:
        return self._repository.list_latest(user_id)


class RecordManualFoodUseCase:
    def __init__(self, repository: CustomFoodRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def preview(
        self,
        *,
        user_id: UUID,
        food_id: UUID,
        food_version_id: UUID,
        amount: Decimal,
        unit: str,
    ) -> tuple[CustomFoodVersion, Decimal, Decimal, ManualNutritionFacts]:
        version = self._repository.find_version(user_id, food_id, food_version_id)
        if version is None:
            raise LookupError("custom food version not found")
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be positive and finite")
        if unit == "servings":
            amount *= version.serving_amount
        elif unit != version.serving_unit:
            raise ValueError("quantity unit does not match the food")
        factor = amount / version.serving_amount
        return version, amount, factor, version.nutrition.scaled(factor)

    def execute(
        self,
        *,
        user_id: UUID,
        food_id: UUID,
        food_version_id: UUID,
        consumed_amount: Decimal,
        consumed_unit: str,
        meal_period: ManualMealPeriod,
        client_event_id: UUID,
    ) -> RecordManualFoodOutcome:
        version, consumed_amount, factor, nutrition = self.preview(
            user_id=user_id,
            food_id=food_id,
            food_version_id=food_version_id,
            amount=consumed_amount,
            unit=consumed_unit,
        )
        unit = version.serving_unit
        provenance = version.provenance
        is_barcode = provenance.authority.value == "open_food_facts"
        source_summary = (
            f"Open Food Facts {provenance.provider_code} snapshot {provenance.payload_sha256}"
            if is_barcode
            else "Owner-entered nutrition frozen at consumption"
        )
        if provenance.serving_authority is not None:
            # The serving basis is owner evidence even though the nutrition is
            # not, so the frozen audit trail must say so explicitly.
            source_summary = f"{source_summary}; serving entered by owner from label"
        entry = ManualFoodConsumptionEntry(
            entry_id=self._ids.new_id(),
            user_id=user_id,
            food_id=food_id,
            food_version_id=food_version_id,
            client_event_id=client_event_id,
            meal_period=meal_period,
            consumed_amount=consumed_amount,
            consumed_unit=unit,
            portion_factor=factor,
            food_name=version.name,
            brand=version.brand,
            serving_description=version.serving_description,
            serving_amount=version.serving_amount,
            serving_unit=version.serving_unit,
            nutrition=nutrition,
            recorded_at=_aware_now(self._clock),
            source_system="barcode_open_food_facts" if is_barcode else "manual_custom",
            nutrition_authority="external_reference" if is_barcode else "user_entered",
            nutrition_confidence="community_database" if is_barcode else "user_entered",
            provenance_summary=source_summary,
        )
        return self._repository.save_consumption(entry)


class AdjustManualFoodUseCase:
    """Append a quantity correction or void without rewriting factual history."""

    def __init__(self, repository: CustomFoodRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def preview(
        self, *, user_id: UUID, entry_id: UUID, amount: Decimal, unit: str
    ) -> tuple[ManualFoodConsumptionEntry, Decimal, Decimal, ManualNutritionFacts]:
        return self._preview(
            user_id=user_id,
            entry_id=entry_id,
            amount=amount,
            unit=unit,
            active_only=True,
        )

    def _preview(
        self,
        *,
        user_id: UUID,
        entry_id: UUID,
        amount: Decimal,
        unit: str,
        active_only: bool,
    ) -> tuple[ManualFoodConsumptionEntry, Decimal, Decimal, ManualNutritionFacts]:
        original = (
            self._repository.find_active_consumption(user_id, entry_id)
            if active_only
            else self._repository.find_consumption(user_id, entry_id)
        )
        if original is None:
            raise LookupError("active manual consumption not found")
        version = self._repository.find_version(user_id, original.food_id, original.food_version_id)
        if version is None:
            raise LookupError("custom food version not found")
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be positive and finite")
        if unit == "servings":
            amount *= version.serving_amount
        elif unit != version.serving_unit:
            raise ValueError("quantity unit does not match the food")
        factor = amount / version.serving_amount
        return original, amount, factor, version.nutrition.scaled(factor)

    def correct(
        self,
        *,
        user_id: UUID,
        entry_id: UUID,
        amount: Decimal,
        unit: str,
        client_event_id: UUID,
    ) -> AdjustManualFoodOutcome:
        original, physical_amount, factor, nutrition = self._preview(
            user_id=user_id,
            entry_id=entry_id,
            amount=amount,
            unit=unit,
            active_only=False,
        )
        now = _aware_now(self._clock)
        replacement = ManualFoodConsumptionEntry(
            entry_id=self._ids.new_id(),
            user_id=user_id,
            food_id=original.food_id,
            food_version_id=original.food_version_id,
            client_event_id=self._ids.new_id(),
            meal_period=original.meal_period,
            consumed_amount=physical_amount,
            consumed_unit=original.serving_unit,
            portion_factor=factor,
            food_name=original.food_name,
            brand=original.brand,
            serving_description=original.serving_description,
            serving_amount=original.serving_amount,
            serving_unit=original.serving_unit,
            nutrition=nutrition,
            recorded_at=now,
            source_system=original.source_system,
            nutrition_authority=original.nutrition_authority,
            nutrition_confidence=original.nutrition_confidence,
            provenance_summary=original.provenance_summary,
        )
        adjustment = ManualFoodConsumptionAdjustment(
            adjustment_id=self._ids.new_id(),
            user_id=user_id,
            client_event_id=client_event_id,
            superseded_entry_id=entry_id,
            replacement_entry_id=replacement.entry_id,
            kind=ManualFoodAdjustmentKind.CORRECTION,
            recorded_at=now,
        )
        return self._repository.save_adjustment(adjustment, replacement)

    def void(
        self, *, user_id: UUID, entry_id: UUID, client_event_id: UUID
    ) -> AdjustManualFoodOutcome:
        if self._repository.find_consumption(user_id, entry_id) is None:
            raise LookupError("manual consumption not found")
        adjustment = ManualFoodConsumptionAdjustment(
            adjustment_id=self._ids.new_id(),
            user_id=user_id,
            client_event_id=client_event_id,
            superseded_entry_id=entry_id,
            replacement_entry_id=None,
            kind=ManualFoodAdjustmentKind.VOID,
            recorded_at=_aware_now(self._clock),
        )
        return self._repository.save_adjustment(adjustment, None)


def _aware_now(clock: Clock) -> datetime:
    value = clock.now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value


__all__ = [
    "AdjustManualFoodUseCase",
    "CreateCustomFoodUseCase",
    "ListCustomFoodsUseCase",
    "RecordManualFoodUseCase",
]
