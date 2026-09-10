"""Pure evidence-bounded longitudinal progress projections (M18)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from nutrition_agent.domain.health.trend import BodyMassTrendSummary, DailyBodyMass
from nutrition_agent.domain.nutrition.history import (
    CalorieAdherence,
    DailyTargetStatus,
    ProteinAdherence,
    build_nutrition_history_day,
)
from nutrition_agent.domain.nutrition.ledger import (
    DailyNutritionLedger,
    DailyNutritionTarget,
    NutritionAuthority,
)
from nutrition_agent.domain.target_review import GoalBandInterpretation, GoalPolicyVersion

PROGRESS_POLICY_VERSION = "owner-longitudinal-progress.v1"
BODY_WINDOW_DAYS = 90
NUTRITION_WINDOW_DAYS = 28
TRAILING_SUMMARY_DAYS = 7

LOGGING_COVERAGE_LIMITATION = "recorded_events_do_not_prove_complete_intake"
RECORD_TIME_LIMITATION = "consumption_is_attributed_by_server_recorded_time"
NO_CAUSALITY_LIMITATION = "nutrition_and_body_weight_are_descriptive_not_causal"


class RecordedNutrientState(StrEnum):
    NO_RECORDS = "no_records"
    QUANTIFIED = "quantified"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EvidenceCount:
    key: str
    count: int


@dataclass(frozen=True)
class ProgressNutritionDay:
    local_date: date
    has_recorded_events: bool
    recorded_event_count: int
    recorded_calories_state: RecordedNutrientState
    recorded_calories_kcal: Decimal | None
    recorded_protein_state: RecordedNutrientState
    recorded_protein_g: Decimal | None
    calorie_target_comparison: CalorieAdherence
    protein_target_comparison: ProteinAdherence
    target_status: DailyTargetStatus
    target: DailyNutritionTarget | None
    authority_counts: tuple[EvidenceCount, ...]
    source_counts: tuple[EvidenceCount, ...]
    includes_estimates: bool


@dataclass(frozen=True)
class CalorieComparisonCounts:
    eligible_day_count: int
    below: int
    at: int
    above: int
    unavailable: int


@dataclass(frozen=True)
class ProteinComparisonCounts:
    eligible_day_count: int
    below: int
    at_or_above: int
    unavailable: int


@dataclass(frozen=True)
class NutrientProgressSummary:
    quantified_recorded_days: int
    partial_recorded_days: int
    unavailable_recorded_days: int
    average_recorded_value: Decimal | None
    average_denominator_days: int


@dataclass(frozen=True)
class ProgressNutritionSummary:
    window_days: int
    start_date: date
    end_date: date
    days_with_recorded_events: int
    days_without_recorded_events: int
    calories: NutrientProgressSummary
    protein: NutrientProgressSummary
    calorie_target_comparisons: CalorieComparisonCounts
    protein_target_comparisons: ProteinComparisonCounts
    authority_event_counts: tuple[EvidenceCount, ...]
    source_event_counts: tuple[EvidenceCount, ...]
    includes_estimates: bool


@dataclass(frozen=True)
class TargetChangeAnnotation:
    effective_at: datetime
    effective_local_date: date
    target_policy_version_id: UUID
    target_policy_version: str
    calories_kcal: Decimal | None
    calories_goal_kind: str | None
    protein_g: Decimal | None
    protein_goal_kind: str | None


@dataclass(frozen=True)
class LongitudinalProgress:
    policy_version: str
    as_of_date: date
    timezone: str
    body_window_start_date: date
    body_window_end_date: date
    body_daily_medians: tuple[DailyBodyMass, ...]
    body_trend_28d: BodyMassTrendSummary
    current_goal: GoalPolicyVersion | None
    goal_interpretation: GoalBandInterpretation | None
    nutrition_window_start_date: date
    nutrition_window_end_date: date
    nutrition_days: tuple[ProgressNutritionDay, ...]
    nutrition_summary_7d: ProgressNutritionSummary
    nutrition_summary_28d: ProgressNutritionSummary
    target_changes: tuple[TargetChangeAnnotation, ...]
    limitation_codes: tuple[str, ...]


def _counts(values: tuple[str, ...]) -> tuple[EvidenceCount, ...]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple(EvidenceCount(key=key, count=counts[key]) for key in sorted(counts))


def _nutrient_state(
    ledger: DailyNutritionLedger,
    attribute: str,
) -> tuple[RecordedNutrientState, Decimal | None]:
    if not ledger.consumed_items:
        return RecordedNutrientState.NO_RECORDS, None
    values = tuple(getattr(item, attribute) for item in ledger.consumed_items)
    known = tuple(value for value in values if isinstance(value, Decimal))
    if len(known) == len(values):
        return RecordedNutrientState.QUANTIFIED, sum(known, Decimal(0))
    if known:
        return RecordedNutrientState.PARTIAL, sum(known, Decimal(0))
    return RecordedNutrientState.UNAVAILABLE, None


def build_progress_nutrition_day(
    ledger: DailyNutritionLedger,
    target_status: DailyTargetStatus,
) -> ProgressNutritionDay:
    """Compact one ledger day while retaining per-nutrient evidence semantics."""

    history_day = build_nutrition_history_day(ledger, target_status)
    calorie_state, calories = _nutrient_state(ledger, "calories_kcal")
    protein_state, protein = _nutrient_state(ledger, "protein_g")
    authorities = tuple(item.authority.value for item in ledger.consumed_items)
    sources = tuple(item.source_system for item in ledger.consumed_items)
    return ProgressNutritionDay(
        local_date=ledger.local_date,
        has_recorded_events=bool(ledger.consumed_items),
        recorded_event_count=len(ledger.consumed_items),
        recorded_calories_state=calorie_state,
        recorded_calories_kcal=calories,
        recorded_protein_state=protein_state,
        recorded_protein_g=protein,
        calorie_target_comparison=history_day.calorie_adherence,
        protein_target_comparison=history_day.protein_adherence,
        target_status=target_status,
        target=ledger.target,
        authority_counts=_counts(authorities),
        source_counts=_counts(sources),
        includes_estimates=any(
            item.authority in {NutritionAuthority.ESTIMATED, NutritionAuthority.PARTIAL}
            for item in ledger.consumed_items
        ),
    )


def _nutrient_summary(
    days: tuple[ProgressNutritionDay, ...],
    *,
    state_attribute: str,
    value_attribute: str,
) -> NutrientProgressSummary:
    quantified = tuple(
        day for day in days if getattr(day, state_attribute) is RecordedNutrientState.QUANTIFIED
    )
    values = tuple(getattr(day, value_attribute) for day in quantified)
    exact_values = tuple(value for value in values if isinstance(value, Decimal))
    if len(exact_values) != len(quantified):
        raise ValueError("quantified progress day is missing its recorded value")
    return NutrientProgressSummary(
        quantified_recorded_days=len(quantified),
        partial_recorded_days=sum(
            getattr(day, state_attribute) is RecordedNutrientState.PARTIAL for day in days
        ),
        unavailable_recorded_days=sum(
            getattr(day, state_attribute) is RecordedNutrientState.UNAVAILABLE for day in days
        ),
        average_recorded_value=(
            sum(exact_values, Decimal(0)) / Decimal(len(exact_values)) if exact_values else None
        ),
        average_denominator_days=len(exact_values),
    )


def summarize_progress_nutrition(
    days: tuple[ProgressNutritionDay, ...],
) -> ProgressNutritionSummary:
    if not days:
        raise ValueError("progress nutrition summary requires at least one day")
    expected = tuple(
        date.fromordinal(days[0].local_date.toordinal() + offset) for offset in range(len(days))
    )
    if tuple(day.local_date for day in days) != expected:
        raise ValueError("progress nutrition days must be contiguous and ascending")

    calorie_below = sum(
        day.calorie_target_comparison is CalorieAdherence.BELOW_TARGET for day in days
    )
    calorie_at = sum(day.calorie_target_comparison is CalorieAdherence.AT_TARGET for day in days)
    calorie_above = sum(
        day.calorie_target_comparison is CalorieAdherence.ABOVE_TARGET for day in days
    )
    protein_below = sum(
        day.protein_target_comparison is ProteinAdherence.BELOW_TARGET for day in days
    )
    protein_met = sum(
        day.protein_target_comparison is ProteinAdherence.AT_OR_ABOVE_TARGET for day in days
    )
    authority_values = tuple(
        key for day in days for item in day.authority_counts for key in (item.key,) * item.count
    )
    source_values = tuple(
        key for day in days for item in day.source_counts for key in (item.key,) * item.count
    )
    with_events = sum(day.has_recorded_events for day in days)
    return ProgressNutritionSummary(
        window_days=len(days),
        start_date=days[0].local_date,
        end_date=days[-1].local_date,
        days_with_recorded_events=with_events,
        days_without_recorded_events=len(days) - with_events,
        calories=_nutrient_summary(
            days,
            state_attribute="recorded_calories_state",
            value_attribute="recorded_calories_kcal",
        ),
        protein=_nutrient_summary(
            days,
            state_attribute="recorded_protein_state",
            value_attribute="recorded_protein_g",
        ),
        calorie_target_comparisons=CalorieComparisonCounts(
            eligible_day_count=calorie_below + calorie_at + calorie_above,
            below=calorie_below,
            at=calorie_at,
            above=calorie_above,
            unavailable=len(days) - calorie_below - calorie_at - calorie_above,
        ),
        protein_target_comparisons=ProteinComparisonCounts(
            eligible_day_count=protein_below + protein_met,
            below=protein_below,
            at_or_above=protein_met,
            unavailable=len(days) - protein_below - protein_met,
        ),
        authority_event_counts=_counts(authority_values),
        source_event_counts=_counts(source_values),
        includes_estimates=any(day.includes_estimates for day in days),
    )


__all__ = [
    "BODY_WINDOW_DAYS",
    "LOGGING_COVERAGE_LIMITATION",
    "NO_CAUSALITY_LIMITATION",
    "NUTRITION_WINDOW_DAYS",
    "PROGRESS_POLICY_VERSION",
    "RECORD_TIME_LIMITATION",
    "TRAILING_SUMMARY_DAYS",
    "CalorieComparisonCounts",
    "EvidenceCount",
    "LongitudinalProgress",
    "NutrientProgressSummary",
    "ProgressNutritionDay",
    "ProgressNutritionSummary",
    "ProteinComparisonCounts",
    "RecordedNutrientState",
    "TargetChangeAnnotation",
    "build_progress_nutrition_day",
    "summarize_progress_nutrition",
]
