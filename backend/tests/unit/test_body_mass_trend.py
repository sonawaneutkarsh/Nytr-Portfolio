"""Pure deterministic tests for the M9 body-mass trend contract."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.domain.health.trend import (
    BODY_MASS_TREND_ALGORITHM_VERSION,
    BodyMassObservation,
    BodyMassTrendStatus,
    DailyBodyMass,
    InvalidBodyMassTrendTimezone,
    aggregate_daily_body_mass,
    analysis_window_utc_bounds,
    calculate_body_mass_trend,
    theil_sen_weekly_rate_kg,
    trailing_average_kg,
)

USER = UUID(int=0xA)
AS_OF = date(2026, 8, 28)


def _observation(
    day: date | None = None,
    value: str = "70",
    *,
    sample_id: int = 1,
    measured_at: datetime | None = None,
) -> BodyMassObservation:
    timestamp = measured_at or datetime.combine(day or AS_OF, datetime.min.time(), tzinfo=UTC)
    return BodyMassObservation(UUID(int=sample_id), Decimal(value), timestamp)


def _daily(offset: int, value: str) -> DailyBodyMass:
    return DailyBodyMass(AS_OF + timedelta(days=offset), Decimal(value), 1)


def _trend(observations: tuple[BodyMassObservation, ...], **kwargs: object):
    return calculate_body_mass_trend(
        user_id=kwargs.get("user_id", USER),  # type: ignore[arg-type]
        observations=observations,
        as_of_date=kwargs.get("as_of_date", AS_OF),  # type: ignore[arg-type]
        timezone=kwargs.get("timezone", "UTC"),  # type: ignore[arg-type]
        algorithm_version=kwargs.get(  # type: ignore[arg-type]
            "algorithm_version", BODY_MASS_TREND_ALGORITHM_VERSION
        ),
    )


def test_observation_requires_aware_timestamp_and_positive_finite_decimal() -> None:
    with pytest.raises(TypeError, match="must be a Decimal"):
        BodyMassObservation(UUID(int=1), 70, datetime(2026, 8, 28, tzinfo=UTC))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        _observation(measured_at=datetime(2026, 8, 28), value="70")
    for value in ("0", "-1", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="positive finite Decimal"):
            _observation(value=value)


def test_daily_body_mass_requires_decimal_value_and_positive_count() -> None:
    with pytest.raises(TypeError, match="must be a Decimal"):
        DailyBodyMass(AS_OF, 70, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive and finite"):
        DailyBodyMass(AS_OF, Decimal("NaN"), 1)
    with pytest.raises(ValueError, match="observation_count"):
        DailyBodyMass(AS_OF, Decimal("70"), 0)


def test_daily_aggregation_empty_and_one_measurement() -> None:
    assert aggregate_daily_body_mass((), "UTC") == ()
    assert aggregate_daily_body_mass((_observation(value="70.125"),), "UTC") == (
        DailyBodyMass(AS_OF, Decimal("70.125"), 1),
    )


def test_daily_aggregation_exact_odd_and_even_medians() -> None:
    odd = tuple(
        _observation(value=value, sample_id=index)
        for index, value in enumerate(("71", "69", "70"), start=1)
    )
    even = tuple(
        _observation(value=value, sample_id=index)
        for index, value in enumerate(("70.004", "70.001", "70.003", "70.002"), start=1)
    )
    assert aggregate_daily_body_mass(odd, "UTC")[0].median_kg == Decimal("70")
    result = aggregate_daily_body_mass(even, "UTC")[0]
    assert result.median_kg == Decimal("70.0025")
    assert result.observation_count == 4


def test_daily_aggregation_is_independent_of_input_order() -> None:
    inputs = (
        _observation(AS_OF - timedelta(days=1), "69", sample_id=1),
        _observation(AS_OF, "72", sample_id=2),
        _observation(AS_OF, "70", sample_id=3),
    )
    assert aggregate_daily_body_mass(inputs, "UTC") == aggregate_daily_body_mass(
        tuple(reversed(inputs)), "UTC"
    )


def test_local_date_is_derived_after_timezone_conversion() -> None:
    observations = (
        _observation(measured_at=datetime(2026, 8, 28, 3, 30, tzinfo=UTC), sample_id=1),
        _observation(
            measured_at=datetime(2026, 8, 28, 4, 30, tzinfo=UTC),
            value="72",
            sample_id=2,
        ),
    )
    daily = aggregate_daily_body_mass(observations, "America/New_York")
    assert tuple(point.local_date for point in daily) == (date(2026, 8, 27), date(2026, 8, 28))


def test_dst_spring_and_fall_grouping_uses_real_iana_rules() -> None:
    spring = (
        _observation(measured_at=datetime(2026, 3, 8, 4, 30, tzinfo=UTC), sample_id=1),
        _observation(measured_at=datetime(2026, 3, 8, 7, 30, tzinfo=UTC), sample_id=2),
    )
    assert tuple(
        point.local_date for point in aggregate_daily_body_mass(spring, "America/New_York")
    ) == (
        date(2026, 3, 7),
        date(2026, 3, 8),
    )
    fall = (
        _observation(measured_at=datetime(2026, 11, 1, 4, 30, tzinfo=UTC), sample_id=3),
        _observation(measured_at=datetime(2026, 11, 1, 6, 30, tzinfo=UTC), sample_id=4),
    )
    result = aggregate_daily_body_mass(fall, "America/New_York")
    assert result == (DailyBodyMass(date(2026, 11, 1), Decimal("70"), 2),)


@pytest.mark.parametrize("timezone", ["UTC", "America/New_York", "Asia/Kolkata"])
def test_analysis_bounds_are_exact_local_calendar_midnights(timezone: str) -> None:
    start, end = analysis_window_utc_bounds(AS_OF, timezone)
    assert start.tzinfo is UTC and end.tzinfo is UTC
    assert (end - start) in {
        timedelta(days=28),
        timedelta(days=28, hours=1),
        timedelta(days=27, hours=23),
    }


def test_dst_analysis_bounds_are_not_a_fixed_offset_table() -> None:
    start, end = analysis_window_utc_bounds(date(2026, 3, 8), "America/New_York")
    assert start == datetime(2026, 2, 9, 5, tzinfo=UTC)
    assert end == datetime(2026, 3, 9, 4, tzinfo=UTC)


def test_invalid_timezone_fails_explicitly() -> None:
    with pytest.raises(InvalidBodyMassTrendTimezone, match="not recognized"):
        aggregate_daily_body_mass((), "Mars/Olympus_Mons")


def test_trailing_average_requires_three_represented_days_and_does_not_fill_gaps() -> None:
    two = (_daily(-6, "69"), _daily(0, "71"))
    three = (_daily(-6, "69"), _daily(-2, "70"), _daily(0, "71"))
    assert trailing_average_kg(two, AS_OF) is None
    assert trailing_average_kg(three, AS_OF) == Decimal("70")


def test_trailing_average_uses_daily_medians_not_raw_sample_weighting() -> None:
    observations = tuple(
        [_observation(AS_OF - timedelta(days=2), "60", sample_id=1)]
        + [_observation(AS_OF - timedelta(days=1), "90", sample_id=index) for index in range(2, 12)]
        + [_observation(AS_OF, "60", sample_id=12)]
    )
    daily = aggregate_daily_body_mass(observations, "UTC")
    assert trailing_average_kg(daily, AS_OF) == Decimal("70")


def test_theil_sen_constant_linear_and_decreasing_rates() -> None:
    constant = tuple(_daily(offset, "70") for offset in (-6, -3, 0))
    increasing = (_daily(-4, "68"), _daily(-2, "70"), _daily(0, "72"))
    decreasing = (_daily(-4, "72"), _daily(-2, "70"), _daily(0, "68"))
    assert theil_sen_weekly_rate_kg(constant) == Decimal("0")
    assert theil_sen_weekly_rate_kg(increasing) == Decimal("7")
    assert theil_sen_weekly_rate_kg(decreasing) == Decimal("-7")


def test_theil_sen_irregular_dates_and_outlier_resistance_are_exact() -> None:
    irregular = (_daily(-10, "65"), _daily(-6, "67"), _daily(0, "70"))
    assert theil_sen_weekly_rate_kg(irregular) == Decimal("3.5")
    outlier = tuple(
        _daily(offset, value)
        for offset, value in zip((-4, -3, -2, -1, 0), ("70", "71", "100", "73", "74"), strict=True)
    )
    assert theil_sen_weekly_rate_kg(outlier) == Decimal("7")
    assert theil_sen_weekly_rate_kg(tuple(reversed(outlier))) == Decimal("7")


def test_theil_sen_needs_two_distinct_daily_points() -> None:
    assert theil_sen_weekly_rate_kg(()) is None
    assert theil_sen_weekly_rate_kg((_daily(0, "70"),)) is None


def test_no_data_excludes_observations_outside_the_28_day_window() -> None:
    summary = _trend(
        (
            _observation(AS_OF - timedelta(days=28), sample_id=1),
            _observation(AS_OF + timedelta(days=1), sample_id=2),
        )
    )
    assert summary.status is BodyMassTrendStatus.NO_DATA
    assert summary.represented_day_count == 0
    assert summary.trailing_7d_average_kg is None and summary.weekly_rate_kg is None


def test_ready_status_exact_metadata_and_optional_trailing_average() -> None:
    offsets = (-20, -18, -16, -14, -12, -6, 0)
    observations = tuple(
        _observation(AS_OF + timedelta(days=offset), str(70 + index), sample_id=index + 1)
        for index, offset in enumerate(offsets)
    )
    summary = _trend(observations)
    assert summary.status is BodyMassTrendStatus.READY
    assert summary.first_measurement_date == AS_OF - timedelta(days=20)
    assert summary.last_measurement_date == AS_OF
    assert summary.latest_measurement_date == AS_OF
    assert summary.latest_measurement_age_days == 0
    assert summary.represented_day_count == 7
    assert summary.coverage_span_days == 20
    assert summary.trailing_7d_average_kg is None  # only two recent days; ready threshold is two
    assert summary.weekly_rate_kg is not None


def test_insufficient_status_for_too_few_days_or_short_span() -> None:
    too_few = tuple(
        _observation(AS_OF + timedelta(days=offset), sample_id=index + 1)
        for index, offset in enumerate((-14, -7, 0))
    )
    short_span = tuple(
        _observation(AS_OF - timedelta(days=index), sample_id=index + 1) for index in range(7)
    )
    assert _trend(too_few).status is BodyMassTrendStatus.INSUFFICIENT
    assert _trend(short_span).status is BodyMassTrendStatus.INSUFFICIENT


def test_staleness_precedes_insufficiency_and_uses_local_measurement_age() -> None:
    stale = tuple(
        _observation(AS_OF + timedelta(days=offset), sample_id=index + 1)
        for index, offset in enumerate((-27, -24, -21, -18, -15, -11, -8))
    )
    summary = _trend(stale)
    assert summary.latest_measurement_age_days == 8
    assert summary.status is BodyMassTrendStatus.STALE


def test_exact_seven_day_age_is_not_stale_but_eight_days_is() -> None:
    age_seven = _trend((_observation(AS_OF - timedelta(days=7)),))
    age_eight = _trend((_observation(AS_OF - timedelta(days=8)),))
    assert age_seven.status is BodyMassTrendStatus.INSUFFICIENT
    assert age_eight.status is BodyMassTrendStatus.STALE


def test_digest_is_order_invariant_and_stable_across_decimal_scale() -> None:
    observations = (
        _observation(AS_OF - timedelta(days=1), "70.0", sample_id=1),
        _observation(AS_OF, "71.000", sample_id=2),
    )
    assert _trend(observations).input_digest == _trend(tuple(reversed(observations))).input_digest
    rescaled = (
        _observation(AS_OF - timedelta(days=1), "70.000", sample_id=1),
        _observation(AS_OF, "71", sample_id=2),
    )
    assert _trend(observations).input_digest == _trend(rescaled).input_digest


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("weight", "70.1"),
        ("timezone", "America/New_York"),
        ("as_of_date", AS_OF + timedelta(days=1)),
        ("algorithm_version", "body-mass-trend-v2"),
        ("user_id", UUID(int=0xB)),
        ("sample_id", 2),
    ],
)
def test_digest_changes_for_each_decision_relevant_input(change: str, value: object) -> None:
    base = _observation(AS_OF, "70", sample_id=1)
    kwargs: dict[str, object] = {}
    changed = base
    if change == "weight":
        changed = _observation(AS_OF, str(value), sample_id=1)
    elif change == "sample_id":
        changed = _observation(AS_OF, "70", sample_id=int(value))
    else:
        kwargs[change] = value
    assert _trend((base,)).input_digest != _trend((changed,), **kwargs).input_digest


def test_same_daily_median_with_changed_source_identity_changes_digest_not_math() -> None:
    first = (_observation(AS_OF, "70", sample_id=1),)
    second = (_observation(AS_OF, "70", sample_id=2),)
    assert aggregate_daily_body_mass(first, "UTC") == aggregate_daily_body_mass(second, "UTC")
    assert _trend(first).input_digest != _trend(second).input_digest
