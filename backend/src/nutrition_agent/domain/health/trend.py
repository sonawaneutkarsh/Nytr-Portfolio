"""Deterministic longitudinal body-mass trend calculations (M9).

Raw M5 measurements remain authoritative.  This module derives an on-demand,
Decimal-only summary from active observations without persistence, target
mutation, or planner coupling.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BODY_MASS_TREND_ALGORITHM_VERSION = "body-mass-trend-v1"
ANALYSIS_WINDOW_DAYS = 28
TRAILING_WINDOW_DAYS = 7
MIN_TRAILING_DAYS = 3
MIN_READY_DAYS = 7
MIN_READY_SPAN_DAYS = 14
MIN_READY_RECENT_DAYS = 2
MAX_FRESH_AGE_DAYS = 7


class InvalidBodyMassTrendTimezone(ValueError):
    """The requested timezone is not a recognized IANA timezone."""


class BodyMassTrendStatus(StrEnum):
    NO_DATA = "no_data"
    INSUFFICIENT = "insufficient"
    STALE = "stale"
    READY = "ready"


@dataclass(frozen=True)
class BodyMassObservation:
    """One active authoritative source observation needed by the trend."""

    sample_uuid: UUID
    value_kg: Decimal
    measured_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value_kg, Decimal):
            raise TypeError("body-mass observation value must be a Decimal")
        if not self.value_kg.is_finite() or self.value_kg <= 0:
            raise ValueError("body-mass observation must be a positive finite Decimal")
        if self.measured_at.tzinfo is None or self.measured_at.utcoffset() is None:
            raise ValueError("body-mass observation timestamp must be timezone-aware")


@dataclass(frozen=True)
class DailyBodyMass:
    """Exact median of all active observations on one requested local date."""

    local_date: date
    median_kg: Decimal
    observation_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.median_kg, Decimal):
            raise TypeError("daily body-mass median must be a Decimal")
        if not self.median_kg.is_finite() or self.median_kg <= 0:
            raise ValueError("daily body-mass median must be positive and finite")
        if self.observation_count < 1:
            raise ValueError("daily body-mass observation_count must be positive")


@dataclass(frozen=True)
class BodyMassTrendSummary:
    """Explainable derived body-mass state for one owner/date/timezone input."""

    as_of_date: date
    timezone: str
    algorithm_version: str
    latest_measurement_date: date | None
    latest_measurement_age_days: int | None
    first_measurement_date: date | None
    last_measurement_date: date | None
    represented_day_count: int
    coverage_span_days: int
    trailing_7d_average_kg: Decimal | None
    weekly_rate_kg: Decimal | None
    status: BodyMassTrendStatus
    input_digest: str


def _timezone(name: str) -> ZoneInfo:
    if not name:
        raise InvalidBodyMassTrendTimezone("timezone is not recognized")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidBodyMassTrendTimezone("timezone is not recognized") from exc


def analysis_window_utc_bounds(as_of_date: date, timezone: str) -> tuple[datetime, datetime]:
    """Return the exact half-open UTC range covering the 28 local calendar days."""

    zone = _timezone(timezone)
    first_date = as_of_date - timedelta(days=ANALYSIS_WINDOW_DAYS - 1)
    end_date = as_of_date + timedelta(days=1)
    start = datetime.combine(first_date, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(end_date, time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def aggregate_daily_body_mass(
    observations: tuple[BodyMassObservation, ...], timezone: str
) -> tuple[DailyBodyMass, ...]:
    """Group observations after IANA timezone conversion and take exact medians."""

    zone = _timezone(timezone)
    grouped: dict[date, list[Decimal]] = {}
    for observation in observations:
        local_date = observation.measured_at.astimezone(zone).date()
        grouped.setdefault(local_date, []).append(observation.value_kg)
    return tuple(
        DailyBodyMass(
            local_date=local_date,
            median_kg=_median(grouped[local_date]),
            observation_count=len(grouped[local_date]),
        )
        for local_date in sorted(grouped)
    )


def trailing_average_kg(daily: tuple[DailyBodyMass, ...], as_of_date: date) -> Decimal | None:
    first_date = as_of_date - timedelta(days=TRAILING_WINDOW_DAYS - 1)
    values = [point.median_kg for point in daily if first_date <= point.local_date <= as_of_date]
    if len(values) < MIN_TRAILING_DAYS:
        return None
    return sum(values, Decimal(0)) / Decimal(len(values))


def theil_sen_weekly_rate_kg(daily: tuple[DailyBodyMass, ...]) -> Decimal | None:
    """Return exact median pairwise calendar-day slope, scaled to kg/week."""

    ordered = tuple(sorted(daily, key=lambda point: point.local_date))
    slopes: list[Decimal] = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            day_distance = (second.local_date - first.local_date).days
            if day_distance <= 0:
                continue
            slopes.append((second.median_kg - first.median_kg) / Decimal(day_distance))
    if not slopes:
        return None
    return _median(slopes) * Decimal(7)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _input_digest(
    *,
    user_id: UUID,
    as_of_date: date,
    timezone: str,
    algorithm_version: str,
    observations: tuple[BodyMassObservation, ...],
) -> str:
    canonical_observations = sorted(
        (
            {
                "measured_at": _timestamp_text(observation.measured_at),
                "sample_uuid": str(observation.sample_uuid),
                "value_kg": _decimal_text(observation.value_kg),
            }
            for observation in observations
        ),
        key=lambda item: (item["measured_at"], item["sample_uuid"], item["value_kg"]),
    )
    payload = {
        "algorithm_version": algorithm_version,
        "as_of_date": as_of_date.isoformat(),
        "observations": canonical_observations,
        "timezone": timezone,
        "user_id": str(user_id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def calculate_body_mass_trend(
    *,
    user_id: UUID,
    observations: tuple[BodyMassObservation, ...],
    as_of_date: date,
    timezone: str,
    algorithm_version: str = BODY_MASS_TREND_ALGORITHM_VERSION,
) -> BodyMassTrendSummary:
    """Derive the versioned 28-day summary from exact active observations."""

    if not algorithm_version:
        raise ValueError("algorithm_version must not be empty")
    zone = _timezone(timezone)
    first_window_date = as_of_date - timedelta(days=ANALYSIS_WINDOW_DAYS - 1)
    included = tuple(
        observation
        for observation in observations
        if first_window_date <= observation.measured_at.astimezone(zone).date() <= as_of_date
    )
    daily = aggregate_daily_body_mass(included, timezone)
    digest = _input_digest(
        user_id=user_id,
        as_of_date=as_of_date,
        timezone=timezone,
        algorithm_version=algorithm_version,
        observations=included,
    )
    if not daily:
        return BodyMassTrendSummary(
            as_of_date=as_of_date,
            timezone=timezone,
            algorithm_version=algorithm_version,
            latest_measurement_date=None,
            latest_measurement_age_days=None,
            first_measurement_date=None,
            last_measurement_date=None,
            represented_day_count=0,
            coverage_span_days=0,
            trailing_7d_average_kg=None,
            weekly_rate_kg=None,
            status=BodyMassTrendStatus.NO_DATA,
            input_digest=digest,
        )

    first_date = daily[0].local_date
    last_date = daily[-1].local_date
    age_days = (as_of_date - last_date).days
    coverage_span_days = (last_date - first_date).days
    trailing_start = as_of_date - timedelta(days=TRAILING_WINDOW_DAYS - 1)
    recent_day_count = sum(point.local_date >= trailing_start for point in daily)
    trailing = trailing_average_kg(daily, as_of_date)
    weekly_rate = theil_sen_weekly_rate_kg(daily)

    if age_days > MAX_FRESH_AGE_DAYS:
        status = BodyMassTrendStatus.STALE
    elif (
        len(daily) >= MIN_READY_DAYS
        and coverage_span_days >= MIN_READY_SPAN_DAYS
        and recent_day_count >= MIN_READY_RECENT_DAYS
        and weekly_rate is not None
    ):
        status = BodyMassTrendStatus.READY
    else:
        status = BodyMassTrendStatus.INSUFFICIENT

    return BodyMassTrendSummary(
        as_of_date=as_of_date,
        timezone=timezone,
        algorithm_version=algorithm_version,
        latest_measurement_date=last_date,
        latest_measurement_age_days=age_days,
        first_measurement_date=first_date,
        last_measurement_date=last_date,
        represented_day_count=len(daily),
        coverage_span_days=coverage_span_days,
        trailing_7d_average_kg=trailing,
        weekly_rate_kg=weekly_rate,
        status=status,
        input_digest=digest,
    )
