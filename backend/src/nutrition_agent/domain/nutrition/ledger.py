"""Pure daily nutrition ledger derived from durable consumption evidence.

The ledger deliberately totals only immutable ``eaten`` events supplied by an
owner-scoped repository.  Recommendations are not consumption, missing
nutrition is not zero, and workout energy is not an input to this calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class NutritionAuthority(StrEnum):
    USER_ENTERED = "user_entered"
    EXTERNAL_REFERENCE = "external_reference"
    OFFICIAL = "official"
    ESTIMATED = "estimated"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class NutritionCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ConsumedNutritionEvidence:
    entry_id: UUID
    recorded_at: datetime
    plan_run_id: UUID | None
    plan_version_id: UUID | None
    plan_item_id: UUID | None
    meal_context: str
    candidate_id: str
    item_name: str
    configuration_summary: str | None
    authority: NutritionAuthority
    confidence: str | None
    calories_kcal: Decimal | None
    protein_g: Decimal | None
    unknown_nutrients: tuple[str, ...]
    provenance_summary: str
    source_system: str = "plan"
    custom_food_id: UUID | None = None
    custom_food_version_id: UUID | None = None
    consumed_amount: Decimal | None = None
    consumed_unit: str | None = None

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        for name, value in (
            ("calories_kcal", self.calories_kcal),
            ("protein_g", self.protein_g),
        ):
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal or None")
        object.__setattr__(self, "unknown_nutrients", tuple(sorted(set(self.unknown_nutrients))))


@dataclass(frozen=True)
class DailyNutritionTarget:
    policy_version_id: UUID
    policy_version: str
    calories_kcal: Decimal | None
    calories_goal_kind: str | None
    protein_g: Decimal | None
    protein_goal_kind: str | None

    def __post_init__(self) -> None:
        for name, value in (
            ("calories_kcal", self.calories_kcal),
            ("protein_g", self.protein_g),
        ):
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal or None")


@dataclass(frozen=True)
class DailyNutritionLedger:
    local_date: date
    timezone: str
    target: DailyNutritionTarget | None
    consumed_item_count: int
    known_calories_consumed: Decimal | None
    known_protein_g_consumed: Decimal | None
    remaining_known_calories: Decimal | None
    remaining_known_protein_g: Decimal | None
    nutrition_completeness: NutritionCompleteness
    authorities: tuple[NutritionAuthority, ...]
    unknown_nutrients: tuple[str, ...]
    consumed_items: tuple[ConsumedNutritionEvidence, ...]
    reason_codes: tuple[str, ...]


def _known_sum(
    items: tuple[ConsumedNutritionEvidence, ...],
    attribute: str,
) -> Decimal | None:
    if not items:
        return None
    values = [getattr(item, attribute) for item in items]
    known = [value for value in values if isinstance(value, Decimal)]
    if not known:
        return None
    return sum(known, Decimal(0))


def build_daily_nutrition_ledger(
    *,
    local_date: date,
    timezone: str,
    consumed_items: tuple[ConsumedNutritionEvidence, ...],
    target: DailyNutritionTarget | None,
) -> DailyNutritionLedger:
    """Build one deterministic ledger without querying infrastructure."""

    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA identifier") from exc

    ordered = tuple(sorted(consumed_items, key=lambda item: (item.recorded_at, item.entry_id)))
    for item in ordered:
        if item.recorded_at.astimezone(zone).date() != local_date:
            raise ValueError("consumption evidence falls outside the requested local date")

    calories = _known_sum(ordered, "calories_kcal")
    protein = _known_sum(ordered, "protein_g")
    unknown = tuple(sorted({key for item in ordered for key in item.unknown_nutrients}))

    if not ordered:
        completeness = NutritionCompleteness.UNAVAILABLE
    elif calories is None and protein is None:
        completeness = NutritionCompleteness.UNAVAILABLE
    elif unknown or any(item.calories_kcal is None or item.protein_g is None for item in ordered):
        completeness = NutritionCompleteness.PARTIAL
    else:
        completeness = NutritionCompleteness.COMPLETE

    authority_order = {
        NutritionAuthority.USER_ENTERED: 0,
        NutritionAuthority.EXTERNAL_REFERENCE: 1,
        NutritionAuthority.OFFICIAL: 2,
        NutritionAuthority.ESTIMATED: 3,
        NutritionAuthority.PARTIAL: 4,
        NutritionAuthority.UNKNOWN: 5,
    }
    authorities = tuple(
        sorted({item.authority for item in ordered}, key=lambda value: authority_order[value])
    )

    remaining_calories = None
    remaining_protein = None
    if target is not None:
        if target.calories_kcal is not None and calories is not None:
            remaining_calories = target.calories_kcal - calories
        if target.protein_g is not None and protein is not None:
            remaining_protein = target.protein_g - protein

    reasons: set[str] = set()
    if target is None:
        reasons.add("target_unavailable")
    else:
        if target.calories_kcal is None:
            reasons.add("calorie_target_unavailable")
        if target.protein_g is None:
            reasons.add("protein_target_unavailable")
    if not ordered:
        reasons.add("no_consumption")
    if ordered and calories is None:
        reasons.add("consumed_calories_unavailable")
    if ordered and protein is None:
        reasons.add("consumed_protein_unavailable")
    if completeness is NutritionCompleteness.PARTIAL:
        reasons.add("nutrition_partial")
    if completeness is NutritionCompleteness.UNAVAILABLE:
        reasons.add("nutrition_unavailable")
    if any(
        authority in {NutritionAuthority.ESTIMATED, NutritionAuthority.PARTIAL}
        for authority in authorities
    ):
        reasons.add("estimated_nutrition_present")

    return DailyNutritionLedger(
        local_date=local_date,
        timezone=timezone,
        target=target,
        consumed_item_count=len(ordered),
        known_calories_consumed=calories,
        known_protein_g_consumed=protein,
        remaining_known_calories=remaining_calories,
        remaining_known_protein_g=remaining_protein,
        nutrition_completeness=completeness,
        authorities=authorities,
        unknown_nutrients=unknown,
        consumed_items=ordered,
        reason_codes=tuple(sorted(reasons)),
    )


__all__ = [
    "ConsumedNutritionEvidence",
    "DailyNutritionLedger",
    "DailyNutritionTarget",
    "NutritionAuthority",
    "NutritionCompleteness",
    "build_daily_nutrition_ledger",
]
