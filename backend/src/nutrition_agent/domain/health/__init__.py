"""Health domain: authoritative body-mass sync plus derived trend foundation.

Pure, infrastructure-independent, and Decimal-only (float ban applies; see
tests/unit/test_architecture_guards.py). Raw M5 measurements remain the source
of truth; M9 trend summaries are derived on demand and never mutate targets.
"""

from nutrition_agent.domain.health.entities import (
    BatchOutcome,
    BodyMassSample,
    HealthSyncStatus,
    LatestSample,
    SampleDeletion,
    StoredSample,
    SyncBatch,
)
from nutrition_agent.domain.health.trend import (
    BODY_MASS_TREND_ALGORITHM_VERSION,
    BodyMassObservation,
    BodyMassTrendStatus,
    BodyMassTrendSummary,
    DailyBodyMass,
    InvalidBodyMassTrendTimezone,
    aggregate_daily_body_mass,
    analysis_window_utc_bounds,
    calculate_body_mass_trend,
    theil_sen_weekly_rate_kg,
    trailing_average_kg,
)
from nutrition_agent.domain.health.validation import (
    BatchRejected,
    RejectedField,
    validate_body_mass_batch,
)

__all__ = [
    "BatchOutcome",
    "BatchRejected",
    "BodyMassSample",
    "BODY_MASS_TREND_ALGORITHM_VERSION",
    "BodyMassObservation",
    "BodyMassTrendStatus",
    "BodyMassTrendSummary",
    "DailyBodyMass",
    "HealthSyncStatus",
    "InvalidBodyMassTrendTimezone",
    "LatestSample",
    "RejectedField",
    "SampleDeletion",
    "StoredSample",
    "SyncBatch",
    "aggregate_daily_body_mass",
    "analysis_window_utc_bounds",
    "calculate_body_mass_trend",
    "theil_sen_weekly_rate_kg",
    "trailing_average_kg",
    "validate_body_mass_batch",
]
