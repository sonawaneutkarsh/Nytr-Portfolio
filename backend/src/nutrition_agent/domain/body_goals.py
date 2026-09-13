"""Deterministic Body & Goals evidence and starting-target policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.domain.health.trend import BodyMassTrendStatus
from nutrition_agent.domain.target_review import GoalDirection

PROFILE_POLICY_VERSION = "owner-body-goal-profile.v1"
STARTING_CALORIE_POLICY_VERSION = "mifflin-st-jeor-starting-target.v1"
WAIST_TREND_POLICY_VERSION = "owner-waist-trend.v1"
PHASE_ASSESSMENT_POLICY_VERSION = "owner-phase-assessment.v1"
MAX_WEIGHT_AGE_DAYS = 7


class FormulaSex(StrEnum):
    MALE = "male"
    FEMALE = "female"


class ActivityLevel(StrEnum):
    SEDENTARY = "sedentary"
    LIGHTLY_ACTIVE = "lightly_active"
    MODERATELY_ACTIVE = "moderately_active"
    VERY_ACTIVE = "very_active"


ACTIVITY_MULTIPLIERS = {
    ActivityLevel.SEDENTARY: Decimal("1.2"),
    ActivityLevel.LIGHTLY_ACTIVE: Decimal("1.375"),
    ActivityLevel.MODERATELY_ACTIVE: Decimal("1.55"),
    ActivityLevel.VERY_ACTIVE: Decimal("1.725"),
}

GOAL_ADJUSTMENTS_KCAL = {
    GoalDirection.GAIN: Decimal("200"),
    GoalDirection.MAINTAIN: Decimal("0"),
    GoalDirection.LOSE: Decimal("-300"),
}


class WaistUnit(StrEnum):
    CM = "cm"
    IN = "in"


class WaistTrendStatus(StrEnum):
    NO_DATA = "no_data"
    INSUFFICIENT = "insufficient"
    STALE = "stale"
    READY = "ready"


class PhaseAssessmentStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    ON_TRACK = "on_track"
    OBSERVE = "observe"
    REVIEW_SUGGESTED = "review_suggested"


class StartingTargetDecisionValue(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class BodyGoalProfileVersion:
    profile_id: UUID
    user_id: UUID
    policy_version: str
    height_cm: Decimal
    date_of_birth: date
    formula_sex: FormulaSex
    activity_level: ActivityLevel
    target_weight_kg: Decimal | None
    payload_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.policy_version != PROFILE_POLICY_VERSION:
            raise ValueError("unsupported profile policy version")
        _bounded(self.height_cm, Decimal("100"), Decimal("250"), "height_cm")
        if self.target_weight_kg is not None:
            _bounded(self.target_weight_kg, Decimal("20"), Decimal("400"), "target_weight_kg")
        if self.payload_sha256 != profile_digest(
            self.height_cm,
            self.date_of_birth,
            self.formula_sex,
            self.activity_level,
            self.target_weight_kg,
        ):
            raise ValueError("profile payload digest mismatch")


@dataclass(frozen=True)
class WaistMeasurement:
    measurement_id: UUID
    user_id: UUID
    measured_at: datetime
    value_cm: Decimal
    entered_value: Decimal
    entered_unit: WaistUnit
    provenance: str
    corrects_measurement_id: UUID | None
    recorded_at: datetime

    def __post_init__(self) -> None:
        _bounded(self.value_cm, Decimal("30"), Decimal("250"), "waist")
        if self.entered_value <= 0 or not self.entered_value.is_finite():
            raise ValueError("entered waist must be positive and finite")
        if self.provenance != "owner_entered":
            raise ValueError("waist provenance must be owner_entered")
        if self.measured_at.tzinfo is None or self.recorded_at.tzinfo is None:
            raise ValueError("waist timestamps must be timezone-aware")


@dataclass(frozen=True)
class WaistTrend:
    policy_version: str
    status: WaistTrendStatus
    latest_measurement_date: date | None
    latest_measurement_age_days: int | None
    represented_day_count: int
    coverage_span_days: int
    weekly_rate_cm: Decimal | None


@dataclass(frozen=True)
class StartingCalorieProposal:
    proposal_id: UUID
    user_id: UUID
    profile_id: UUID
    goal_policy_version_id: UUID
    body_mass_sample_uuid: UUID
    policy_version: str
    as_of_date: date
    timezone: str
    age_years: int
    body_mass_kg: Decimal
    bmr_kcal: Decimal
    activity_multiplier: Decimal
    maintenance_kcal: Decimal
    goal_adjustment_kcal: Decimal
    proposed_calorie_kcal: Decimal
    evidence_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class StartingTargetDecision:
    decision_id: UUID
    user_id: UUID
    proposal_id: UUID
    decision: StartingTargetDecisionValue
    client_event_id: UUID
    resulting_target_policy_version_id: UUID | None
    decided_at: datetime


@dataclass(frozen=True)
class PhaseAssessment:
    policy_version: str
    status: PhaseAssessmentStatus
    reason_codes: tuple[str, ...]


def profile_digest(
    height_cm: Decimal,
    date_of_birth: date,
    formula_sex: FormulaSex,
    activity_level: ActivityLevel,
    target_weight_kg: Decimal | None,
) -> str:
    return _digest(
        {
            "activity_level": activity_level.value,
            "date_of_birth": date_of_birth.isoformat(),
            "formula_sex": formula_sex.value,
            "height_cm": str(height_cm),
            "policy_version": PROFILE_POLICY_VERSION,
            "target_weight_kg": str(target_weight_kg) if target_weight_kg is not None else None,
        }
    )


def age_on(date_of_birth: date, as_of_date: date) -> int:
    if date_of_birth > as_of_date:
        raise ValueError("date_of_birth cannot be in the future")
    age = (
        as_of_date.year
        - date_of_birth.year
        - ((as_of_date.month, as_of_date.day) < (date_of_birth.month, date_of_birth.day))
    )
    if age < 18 or age > 120:
        raise ValueError("starting target supports adult ages from 18 through 120")
    return age


def starting_calorie_estimate(
    *,
    weight_kg: Decimal,
    height_cm: Decimal,
    age_years: int,
    formula_sex: FormulaSex,
    activity_level: ActivityLevel,
    goal_direction: GoalDirection,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    _bounded(weight_kg, Decimal("20"), Decimal("400"), "weight_kg")
    _bounded(height_cm, Decimal("100"), Decimal("250"), "height_cm")
    if age_years < 18 or age_years > 120:
        raise ValueError("age_years is outside the supported adult range")
    coefficient = Decimal("5") if formula_sex is FormulaSex.MALE else Decimal("-161")
    bmr = (
        Decimal("10") * weight_kg
        + Decimal("6.25") * height_cm
        - Decimal("5") * Decimal(age_years)
        + coefficient
    )
    multiplier = ACTIVITY_MULTIPLIERS[activity_level]
    maintenance = bmr * multiplier
    adjustment = GOAL_ADJUSTMENTS_KCAL[goal_direction]
    proposed = ((maintenance + adjustment) / Decimal("50")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * Decimal("50")
    if proposed <= 0:
        raise ValueError("starting calorie estimate is not positive")
    return bmr, multiplier, maintenance, proposed


def starting_proposal_digest(proposal: StartingCalorieProposal) -> str:
    return _digest(
        {
            "activity_multiplier": str(proposal.activity_multiplier),
            "age_years": proposal.age_years,
            "as_of_date": proposal.as_of_date.isoformat(),
            "bmr_kcal": str(proposal.bmr_kcal),
            "body_mass_kg": str(proposal.body_mass_kg),
            "body_mass_sample_uuid": str(proposal.body_mass_sample_uuid),
            "goal_adjustment_kcal": str(proposal.goal_adjustment_kcal),
            "goal_policy_version_id": str(proposal.goal_policy_version_id),
            "maintenance_kcal": str(proposal.maintenance_kcal),
            "policy_version": proposal.policy_version,
            "profile_id": str(proposal.profile_id),
            "proposed_calorie_kcal": str(proposal.proposed_calorie_kcal),
            "timezone": proposal.timezone,
            "user_id": str(proposal.user_id),
        }
    )


def waist_value_cm(value: Decimal, unit: WaistUnit) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("waist value must be positive and finite")
    converted = value if unit is WaistUnit.CM else value * Decimal("2.54")
    _bounded(converted, Decimal("30"), Decimal("250"), "waist")
    return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_waist_trend(
    measurements: tuple[WaistMeasurement, ...], as_of_date: date, timezone: str = "UTC"
) -> WaistTrend:
    zone = ZoneInfo(timezone)
    daily: dict[date, list[Decimal]] = {}
    for measurement in measurements:
        day = measurement.measured_at.astimezone(zone).date()
        if 0 <= (as_of_date - day).days <= 89:
            daily.setdefault(day, []).append(measurement.value_cm)
    points = tuple((day, _median(values)) for day, values in sorted(daily.items()))
    if not points:
        return WaistTrend(
            WAIST_TREND_POLICY_VERSION, WaistTrendStatus.NO_DATA, None, None, 0, 0, None
        )
    first, latest = points[0][0], points[-1][0]
    age = (as_of_date - latest).days
    span = (latest - first).days
    rate = _theil_sen(points)
    status = (
        WaistTrendStatus.STALE
        if age > 14
        else WaistTrendStatus.READY
        if len(points) >= 3 and span >= 14 and rate is not None
        else WaistTrendStatus.INSUFFICIENT
    )
    return WaistTrend(WAIST_TREND_POLICY_VERSION, status, latest, age, len(points), span, rate)


def assess_phase(
    *,
    goal_direction: GoalDirection,
    goal_band_status: str,
    weight_status: BodyMassTrendStatus,
    weight_rate_kg: Decimal | None,
    waist: WaistTrend,
) -> PhaseAssessment:
    reasons: tuple[str, ...]
    if weight_status is not BodyMassTrendStatus.READY or waist.status is not WaistTrendStatus.READY:
        return PhaseAssessment(
            PHASE_ASSESSMENT_POLICY_VERSION,
            PhaseAssessmentStatus.UNAVAILABLE,
            ("insufficient_weight_or_waist_evidence",),
        )
    assert weight_rate_kg is not None and waist.weekly_rate_cm is not None
    wr, ar = weight_rate_kg, waist.weekly_rate_cm
    if goal_direction is GoalDirection.GAIN:
        if goal_band_status == "within_band" and ar <= Decimal("0.25"):
            status, reasons = (
                PhaseAssessmentStatus.ON_TRACK,
                ("gain_rate_in_band", "waist_slow_or_stable"),
            )
        elif goal_band_status == "above_band" and ar > Decimal("0.50"):
            status, reasons = (
                PhaseAssessmentStatus.REVIEW_SUGGESTED,
                ("gain_rate_above_band", "waist_increasing_quickly"),
            )
        else:
            status, reasons = PhaseAssessmentStatus.OBSERVE, ("mixed_gain_evidence",)
    elif goal_direction is GoalDirection.LOSE:
        if goal_band_status == "within_band" and ar <= Decimal("-0.10"):
            status, reasons = (
                PhaseAssessmentStatus.ON_TRACK,
                ("loss_rate_in_band", "waist_decreasing"),
            )
        elif wr >= Decimal("-0.05") and abs(ar) <= Decimal("0.25"):
            status, reasons = PhaseAssessmentStatus.REVIEW_SUGGESTED, ("weight_and_waist_flat",)
        else:
            status, reasons = PhaseAssessmentStatus.OBSERVE, ("mixed_loss_evidence",)
    else:
        if abs(wr) <= Decimal("0.10") and abs(ar) <= Decimal("0.25"):
            status, reasons = PhaseAssessmentStatus.ON_TRACK, ("weight_and_waist_stable",)
        elif abs(wr) > Decimal("0.20") or abs(ar) > Decimal("0.50"):
            status, reasons = PhaseAssessmentStatus.REVIEW_SUGGESTED, ("maintenance_drift",)
        else:
            status, reasons = PhaseAssessmentStatus.OBSERVE, ("mixed_maintenance_evidence",)
    return PhaseAssessment(PHASE_ASSESSMENT_POLICY_VERSION, status, reasons)


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _theil_sen(points: tuple[tuple[date, Decimal], ...]) -> Decimal | None:
    slopes: list[Decimal] = []
    for index, first in enumerate(points):
        for second in points[index + 1 :]:
            days = (second[0] - first[0]).days
            if days:
                slopes.append((second[1] - first[1]) / Decimal(days) * Decimal(7))
    return _median(slopes) if slopes else None


def _bounded(value: Decimal, low: Decimal, high: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or not low <= value <= high:
        raise ValueError(f"{name} is outside the supported range")


def _digest(document: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [name for name in globals() if not name.startswith("_")]
