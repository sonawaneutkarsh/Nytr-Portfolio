"""Deterministic seven-day nutrition history and factual adherence labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from nutrition_agent.domain.nutrition.ledger import (
    DailyNutritionLedger,
    NutritionCompleteness,
)


class DailyTargetStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    TARGET_CHANGED_DURING_DAY = "target_changed_during_day"


class CalorieAdherence(StrEnum):
    BELOW_TARGET = "below_target"
    AT_TARGET = "at_target"
    ABOVE_TARGET = "above_target"
    UNAVAILABLE = "unavailable"


class ProteinAdherence(StrEnum):
    BELOW_TARGET = "below_target"
    AT_OR_ABOVE_TARGET = "at_or_above_target"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class NutritionHistoryDay:
    ledger: DailyNutritionLedger
    target_status: DailyTargetStatus
    calorie_adherence: CalorieAdherence
    protein_adherence: ProteinAdherence
    reason_codes: tuple[str, ...]

    @property
    def local_date(self) -> date:
        return self.ledger.local_date

    @property
    def consumed_event_count(self) -> int:
        return self.ledger.consumed_item_count

    @property
    def known_calories_consumed(self) -> Decimal | None:
        return self.ledger.known_calories_consumed

    @property
    def known_protein_g_consumed(self) -> Decimal | None:
        return self.ledger.known_protein_g_consumed

    @property
    def calorie_target(self) -> Decimal | None:
        return self.ledger.target.calories_kcal if self.ledger.target is not None else None

    @property
    def protein_target_g(self) -> Decimal | None:
        return self.ledger.target.protein_g if self.ledger.target is not None else None

    @property
    def remaining_calories(self) -> Decimal | None:
        return self.ledger.remaining_known_calories

    @property
    def remaining_protein_g(self) -> Decimal | None:
        return self.ledger.remaining_known_protein_g

    @property
    def completeness(self) -> NutritionCompleteness:
        return self.ledger.nutrition_completeness


@dataclass(frozen=True)
class NutritionHistorySummary:
    days_with_consumption: int
    days_complete: int
    days_partial: int
    days_unavailable: int
    days_target_available: int
    days_target_changed: int
    known_calories_total: Decimal | None
    known_protein_g_total: Decimal | None


@dataclass(frozen=True)
class NutritionHistory7Day:
    start_date: date
    end_date: date
    timezone: str
    days: tuple[NutritionHistoryDay, ...]
    summary: NutritionHistorySummary


def _known_total(values: tuple[Decimal | None, ...]) -> Decimal | None:
    known = tuple(value for value in values if value is not None)
    if not known:
        return None
    return sum(known, Decimal(0))


def build_nutrition_history_day(
    ledger: DailyNutritionLedger,
    target_status: DailyTargetStatus,
) -> NutritionHistoryDay:
    target = ledger.target
    has_events = ledger.consumed_item_count > 0
    calories_quantified = has_events and all(
        item.calories_kcal is not None for item in ledger.consumed_items
    )
    protein_quantified = has_events and all(
        item.protein_g is not None for item in ledger.consumed_items
    )
    target_available = target_status is DailyTargetStatus.AVAILABLE and target is not None
    calorie = CalorieAdherence.UNAVAILABLE
    protein = ProteinAdherence.UNAVAILABLE
    if target_available and target is not None:
        if (
            calories_quantified
            and ledger.known_calories_consumed is not None
            and target.calories_kcal is not None
            and target.calories_goal_kind == "target"
        ):
            if ledger.known_calories_consumed < target.calories_kcal:
                calorie = CalorieAdherence.BELOW_TARGET
            elif ledger.known_calories_consumed > target.calories_kcal:
                calorie = CalorieAdherence.ABOVE_TARGET
            else:
                calorie = CalorieAdherence.AT_TARGET
        if (
            protein_quantified
            and ledger.known_protein_g_consumed is not None
            and target.protein_g is not None
            and target.protein_goal_kind == "floor"
        ):
            protein = (
                ProteinAdherence.AT_OR_ABOVE_TARGET
                if ledger.known_protein_g_consumed >= target.protein_g
                else ProteinAdherence.BELOW_TARGET
            )

    reasons = set(ledger.reason_codes)
    if target_status is DailyTargetStatus.TARGET_CHANGED_DURING_DAY:
        reasons.add("target_changed_during_day")
    if calorie is CalorieAdherence.UNAVAILABLE or protein is ProteinAdherence.UNAVAILABLE:
        reasons.add("exact_adherence_unavailable")
    return NutritionHistoryDay(
        ledger=ledger,
        target_status=target_status,
        calorie_adherence=calorie,
        protein_adherence=protein,
        reason_codes=tuple(sorted(reasons)),
    )


def build_nutrition_history(
    *,
    timezone: str,
    ledgers: tuple[tuple[DailyNutritionLedger, DailyTargetStatus], ...],
) -> NutritionHistory7Day:
    """Build a strict seven-day history from already resolved daily ledgers."""

    if len(ledgers) != 7:
        raise ValueError("nutrition history requires exactly seven days")
    days = tuple(build_nutrition_history_day(ledger, status) for ledger, status in ledgers)
    expected = tuple(
        date.fromordinal(days[0].local_date.toordinal() + offset) for offset in range(7)
    )
    if tuple(day.local_date for day in days) != expected:
        raise ValueError("nutrition history days must be contiguous and ascending")
    if any(day.ledger.timezone != timezone for day in days):
        raise ValueError("nutrition history timezone must match every daily ledger")

    summary = NutritionHistorySummary(
        days_with_consumption=sum(day.ledger.consumed_item_count > 0 for day in days),
        days_complete=sum(
            day.ledger.nutrition_completeness is NutritionCompleteness.COMPLETE for day in days
        ),
        days_partial=sum(
            day.ledger.nutrition_completeness is NutritionCompleteness.PARTIAL for day in days
        ),
        days_unavailable=sum(
            day.ledger.nutrition_completeness is NutritionCompleteness.UNAVAILABLE for day in days
        ),
        days_target_available=sum(day.target_status is DailyTargetStatus.AVAILABLE for day in days),
        days_target_changed=sum(
            day.target_status is DailyTargetStatus.TARGET_CHANGED_DURING_DAY for day in days
        ),
        known_calories_total=_known_total(
            tuple(day.ledger.known_calories_consumed for day in days)
        ),
        known_protein_g_total=_known_total(
            tuple(day.ledger.known_protein_g_consumed for day in days)
        ),
    )
    return NutritionHistory7Day(
        start_date=days[0].local_date,
        end_date=days[-1].local_date,
        timezone=timezone,
        days=days,
        summary=summary,
    )


__all__ = [
    "CalorieAdherence",
    "DailyTargetStatus",
    "NutritionHistory7Day",
    "NutritionHistoryDay",
    "NutritionHistorySummary",
    "ProteinAdherence",
    "build_nutrition_history_day",
    "build_nutrition_history",
]
