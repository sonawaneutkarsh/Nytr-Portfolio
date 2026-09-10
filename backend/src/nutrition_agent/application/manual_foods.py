"""Application orchestration for immutable owner-entered foods and intake."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.ports import Clock, CustomFoodRepository, IdGenerator
from nutrition_agent.domain.nutrition.custom_foods import (
    OWNER_ENTERED_PROVENANCE,
    CustomFoodProvenance,
    CustomFoodVersion,
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
        version = self._repository.find_version(user_id, food_id, food_version_id)
        if version is None:
            raise LookupError("custom food version not found")
        unit = consumed_unit.strip()
        if unit != version.serving_unit:
            raise ValueError("consumed unit must match the food serving unit")
        if (
            not isinstance(consumed_amount, Decimal)
            or not consumed_amount.is_finite()
            or consumed_amount <= 0
        ):
            raise ValueError("consumed amount must be a positive finite Decimal")
        factor = consumed_amount / version.serving_amount
        provenance = version.provenance
        is_barcode = provenance.authority.value == "open_food_facts"
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
            nutrition=version.nutrition.scaled(factor),
            recorded_at=_aware_now(self._clock),
            source_system="barcode_open_food_facts" if is_barcode else "manual_custom",
            nutrition_authority="external_reference" if is_barcode else "user_entered",
            nutrition_confidence="community_database" if is_barcode else "user_entered",
            provenance_summary=(
                f"Open Food Facts {provenance.provider_code} snapshot {provenance.payload_sha256}"
                if is_barcode
                else "Owner-entered nutrition frozen at consumption"
            ),
        )
        return self._repository.save_consumption(entry)


def _aware_now(clock: Clock) -> datetime:
    value = clock.now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value


__all__ = [
    "CreateCustomFoodUseCase",
    "ListCustomFoodsUseCase",
    "RecordManualFoodUseCase",
]
