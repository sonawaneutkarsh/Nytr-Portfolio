"""Deterministic Body & Goals setup and phase-progress interpretation.

This module deliberately keeps HealthKit body mass authoritative.  Profile
height and owner-entered waist measurements are supplementary inputs; neither
can silently create or change an approved calorie target.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.target_review import GoalDirection, GoalPolicyVersion

BODY_GOALS_POLICY_VERSION = "owner-body-goals.v1"
STARTING_CALORIE_ESTIMATE_POLICY_VERSION = "owner-starting-calories.v1"


def _decimal(name: str, value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal")
    return value


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class OwnerBodyProfile:
    user_id: UUID
    height_cm: Decimal
    target_weight_kg: Decimal | None
    updated_at: datetime

    def __post_init__(self) -> None:
        height = _decimal("height_cm", self.height_cm)
        if not Decimal("100") <= height <= Decimal("250"):
            raise ValueError("height_cm must be between 100 and 250")
        if self.target_weight_kg is not None:
            target = _decimal("target_weight_kg", self.target_weight_kg)
            if not Decimal("20") <= target <= Decimal("400"):
                raise ValueError("target_weight_kg must be between 20 and 400")
        _aware("profile updated_at", self.updated_at)


@dataclass(frozen=True)
class WaistMeasurement:
    measurement_id: UUID
    user_id: UUID
    waist_cm: Decimal
    measured_at: datetime
    recorded_at: datetime
    source: str = "owner_manual"

    def __post_init__(self) -> None:
        waist = _decimal("waist_cm", self.waist_cm)
        if not Decimal("30") <= waist <= Decimal("250"):
            raise ValueError("waist_cm must be between 30 and 250")
        _aware("waist measured_at", self.measured_at)
        _aware("waist recorded_at", self.recorded_at)
        if self.source != "owner_manual":
            raise ValueError("waist source must be owner_manual")


class PhaseProgressStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    ON_TRACK = "on_track"
    REVIEW = "review"


@dataclass(frozen=True)
class PhaseProgress:
    status: PhaseProgressStatus
    direction: GoalDirection
    reason: str
    weight_rate_kg_per_week: Decimal | None
    waist_rate_cm_per_week: Decimal | None


@dataclass(frozen=True)
class StartingCalorieEstimate:
    policy_version: str
    estimate_kcal: int
    weight_kg: Decimal
    rationale: str
    confidence: str = "estimated"


def estimate_starting_calories(weight_kg: Decimal) -> StartingCalorieEstimate:
    """Return a bounded weight-only starting estimate, never a maintenance claim.

    Without age/sex/validated activity inputs, a demographic BMR/TDEE formula
    would imply unsupported precision.  The portfolio policy uses a transparent
    34 kcal/kg anchor, rounds to 50 kcal, and bounds the result.  Longitudinal
    observed weight trend remains more important once available.
    """

    weight = _decimal("weight_kg", weight_kg)
    if not Decimal("20") <= weight <= Decimal("400"):
        raise ValueError("weight_kg must be between 20 and 400")
    raw = ((weight * Decimal("34")) / Decimal("50")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * Decimal("50")
    kcal = int(min(Decimal("4000"), max(Decimal("1600"), raw)))
    return StartingCalorieEstimate(
        policy_version=STARTING_CALORIE_ESTIMATE_POLICY_VERSION,
        estimate_kcal=kcal,
        weight_kg=weight,
        rationale=(
            "Bounded owner-starting estimate: 34 kcal/kg, rounded to the nearest "
            "50 kcal and bounded to 1,600–4,000 kcal. This is an estimate, not "
            "measured maintenance; observed HealthKit weight trend is preferred "
            "for later Target Review. No exercise calorie eat-back is included."
        ),
    )


def _waist_weekly_rate(measurements: tuple[WaistMeasurement, ...]) -> Decimal | None:
    ordered = sorted(measurements, key=lambda item: item.measured_at.astimezone(UTC))
    if len(ordered) < 2:
        return None
    first, last = ordered[0], ordered[-1]
    days = (
        last.measured_at.astimezone(UTC) - first.measured_at.astimezone(UTC)
    ).total_seconds() / 86400
    if days < 7:
        return None
    return (last.waist_cm - first.waist_cm) / Decimal(str(days)) * Decimal("7")


def evaluate_phase_progress(
    *,
    goal: GoalPolicyVersion,
    trend: BodyMassTrendSummary,
    waist_measurements: tuple[WaistMeasurement, ...],
) -> PhaseProgress:
    """Interpret progress without body-fat claims or automatic phase changes."""

    waist_rate = _waist_weekly_rate(waist_measurements)
    if trend.status is not BodyMassTrendStatus.READY or trend.weekly_rate_kg is None:
        return PhaseProgress(
            status=PhaseProgressStatus.UNAVAILABLE,
            direction=goal.direction,
            reason="sufficient HealthKit weight trend evidence is not available",
            weight_rate_kg_per_week=trend.weekly_rate_kg,
            waist_rate_cm_per_week=waist_rate,
        )
    rate = trend.weekly_rate_kg
    desired = goal.desired_rate_kg_per_week
    if goal.direction is GoalDirection.GAIN:
        too_fast = rate > desired + Decimal("0.15") or (
            waist_rate is not None and waist_rate > Decimal("0.30")
        )
        status = PhaseProgressStatus.REVIEW if too_fast else PhaseProgressStatus.ON_TRACK
        reason = (
            "weight gain is faster than the intended band or waist is rising quickly"
            if too_fast
            else "weight gain is within the intended band; waist is supplementary evidence"
        )
    elif goal.direction is GoalDirection.LOSE:
        too_slow = rate >= Decimal("0")
        waist_concern = waist_rate is not None and waist_rate >= Decimal("0")
        status = (
            PhaseProgressStatus.REVIEW
            if too_slow or waist_concern
            else PhaseProgressStatus.ON_TRACK
        )
        reason = (
            "weight or waist trend does not show a sustained decrease"
            if status is PhaseProgressStatus.REVIEW
            else "weight is decreasing; waist is supplementary evidence"
        )
    else:
        deviation = abs(rate)
        waist_deviation = waist_rate is not None and abs(waist_rate) > Decimal("0.30")
        status = (
            PhaseProgressStatus.REVIEW
            if deviation > Decimal("0.15") or waist_deviation
            else PhaseProgressStatus.ON_TRACK
        )
        reason = (
            "weight or waist trend has sustained deviation"
            if status is PhaseProgressStatus.REVIEW
            else "weight is stable; waist is supplementary evidence"
        )
    return PhaseProgress(status, goal.direction, reason, rate, waist_rate)


__all__ = [
    "BODY_GOALS_POLICY_VERSION",
    "STARTING_CALORIE_ESTIMATE_POLICY_VERSION",
    "OwnerBodyProfile",
    "PhaseProgress",
    "PhaseProgressStatus",
    "StartingCalorieEstimate",
    "WaistMeasurement",
    "estimate_starting_calories",
    "evaluate_phase_progress",
]
