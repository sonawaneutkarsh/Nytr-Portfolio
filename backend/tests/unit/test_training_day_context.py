"""Deterministic M12B training-day policy and context regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.training_context import TrainingDayContextUseCase
from nutrition_agent.db.in_memory_repos import InMemoryTrainingSessionRepository
from nutrition_agent.domain.training import (
    InvalidDailyHealthTimezone,
    StoredTrainingSession,
    TrainingSessionObservation,
    TrainingSourceSystem,
    TrainingSyncBatch,
)
from nutrition_agent.domain.training.context import (
    OWNER_STRENGTH_TRAINING_DAY_POLICY_V1,
    HealthKitWorkoutActivityType,
    NormalizedWorkoutKind,
    TrainingDayAttribution,
    TrainingDayContext,
    TrainingDayReasonCode,
    decode_healthkit_activity_type,
    evaluate_training_day_context,
    normalized_workout_kind,
    qualify_training_session,
)

DAY = date(2026, 9, 3)
USER = UUID(int=1)
INGESTED_AT = datetime(2026, 9, 4, tzinfo=UTC)
TRADITIONAL_STRENGTH = "50"
FUNCTIONAL_STRENGTH = "20"
RUNNING = "37"
WALKING = "52"


def _session(
    session_number: int,
    *,
    source_record_number: int | None = None,
    activity_type: str | None = TRADITIONAL_STRENGTH,
    start: datetime = datetime(2026, 9, 3, 14, tzinfo=UTC),
    minutes: str = "60",
    duration: Decimal | None = None,
    energy: str | None = "400",
    source_name: str | None = "Apple Watch",
    source_bundle_id: str | None = "com.apple.health",
    tombstoned: bool = False,
) -> StoredTrainingSession:
    duration_seconds = duration
    if duration_seconds is None and minutes != "missing":
        duration_seconds = Decimal(minutes) * Decimal("60")
    wall_duration = Decimal("60") if minutes == "missing" else Decimal(minutes)
    return StoredTrainingSession(
        session_id=UUID(int=session_number),
        user_id=USER,
        source_system=TrainingSourceSystem.HEALTHKIT,
        source_record_id=str(UUID(int=source_record_number or session_number)),
        activity_type=activity_type,
        started_at=start,
        ended_at=start + timedelta(seconds=int(wall_duration * Decimal("60"))),
        active_duration_seconds=duration_seconds,
        active_energy_kcal=Decimal(energy) if energy is not None else None,
        timezone_identifier="America/New_York",
        source_name=source_name,
        source_bundle_id=source_bundle_id,
        source_revision="26.0",
        ingested_at=INGESTED_AT,
        tombstoned_at=INGESTED_AT if tombstoned else None,
    )


def _context(
    *sessions: StoredTrainingSession,
    local_date: date = DAY,
    timezone: str = "America/New_York",
) -> TrainingDayContext:
    return evaluate_training_day_context(
        local_date=local_date,
        timezone=timezone,
        training_sessions=tuple(sessions),
    )


def test_policy_v1_is_explicit_immutable_and_uses_start_time_attribution() -> None:
    policy = OWNER_STRENGTH_TRAINING_DAY_POLICY_V1
    assert policy.version == "owner-strength-training-day.v1"
    assert policy.minimum_duration_seconds == Decimal("1200")
    assert policy.attribution is TrainingDayAttribution.START_TIME
    assert policy.qualifying_workout_kinds == frozenset(
        {
            NormalizedWorkoutKind.FUNCTIONAL_STRENGTH_TRAINING,
            NormalizedWorkoutKind.TRADITIONAL_STRENGTH_TRAINING,
        }
    )


def test_installed_sdk_activity_values_decode_without_changing_raw_observation() -> None:
    expected = {
        "11": HealthKitWorkoutActivityType.CROSS_TRAINING,
        "20": HealthKitWorkoutActivityType.FUNCTIONAL_STRENGTH_TRAINING,
        "33": HealthKitWorkoutActivityType.PREPARATION_AND_RECOVERY,
        "37": HealthKitWorkoutActivityType.RUNNING,
        "50": HealthKitWorkoutActivityType.TRADITIONAL_STRENGTH_TRAINING,
        "52": HealthKitWorkoutActivityType.WALKING,
        "62": HealthKitWorkoutActivityType.FLEXIBILITY,
    }
    assert {raw: decode_healthkit_activity_type(raw) for raw in expected} == expected
    assert decode_healthkit_activity_type("020") is None
    assert decode_healthkit_activity_type("strength") is None


@pytest.mark.parametrize(
    ("minutes", "activity_type", "expected_kind"),
    [
        ("100", TRADITIONAL_STRENGTH, NormalizedWorkoutKind.TRADITIONAL_STRENGTH_TRAINING),
        ("74", FUNCTIONAL_STRENGTH, NormalizedWorkoutKind.FUNCTIONAL_STRENGTH_TRAINING),
    ],
)
def test_production_shaped_hevy_strength_sessions_qualify(
    minutes: str,
    activity_type: str,
    expected_kind: NormalizedWorkoutKind,
) -> None:
    session = _session(
        1,
        activity_type=activity_type,
        minutes=minutes,
        source_name="Hevy",
        source_bundle_id="com.hevyapp.hevy",
    )
    result = qualify_training_session(session)
    assert result.qualifies is True
    assert result.normalized_kind is expected_kind
    assert result.reason_code is TrainingDayReasonCode.QUALIFYING_STRENGTH_SESSION


def test_hevy_provenance_alone_does_not_qualify_contradicting_activity() -> None:
    session = _session(
        1,
        activity_type=RUNNING,
        minutes="100",
        source_name="Hevy",
        source_bundle_id="com.hevyapp.hevy",
    )
    result = qualify_training_session(session)
    assert result.qualifies is False
    assert result.normalized_kind is NormalizedWorkoutKind.OTHER
    assert result.reason_code is TrainingDayReasonCode.UNSUPPORTED_ACTIVITY_TYPE


def test_apple_watch_strength_qualifies_at_exact_boundary() -> None:
    result = qualify_training_session(_session(1, minutes="20"))
    assert result.qualifies is True
    assert result.active_duration_seconds == Decimal("1200")


def test_below_minimum_strength_session_is_rejected_as_noise() -> None:
    result = qualify_training_session(_session(1, minutes="19.999"))
    assert result.qualifies is False
    assert result.reason_code is TrainingDayReasonCode.SESSIONS_BELOW_DURATION


def test_missing_duration_fails_closed_without_using_wall_clock_duration() -> None:
    result = qualify_training_session(_session(1, minutes="missing"))
    assert result.qualifies is False
    assert result.reason_code is TrainingDayReasonCode.MISSING_DURATION


@pytest.mark.parametrize("activity_type", [RUNNING, WALKING, "11", "33", "62", "999", None])
def test_non_strength_or_unknown_activity_is_unsupported(activity_type: str | None) -> None:
    result = qualify_training_session(_session(1, activity_type=activity_type, minutes="100"))
    assert result.qualifies is False
    assert result.reason_code is TrainingDayReasonCode.UNSUPPORTED_ACTIVITY_TYPE


def test_no_session_day_is_explicit_and_not_training() -> None:
    context = _context()
    assert context.is_training_day is False
    assert context.qualifying_session_count == 0
    assert context.qualifying_duration_seconds == Decimal("0")
    assert context.reason_codes == (TrainingDayReasonCode.NO_SESSIONS,)


def test_two_qualifying_sessions_sum_duration_and_explain_sources() -> None:
    context = _context(
        _session(
            1,
            minutes="100",
            source_name="Hevy",
            source_bundle_id="com.hevyapp.hevy",
        ),
        _session(2, activity_type=FUNCTIONAL_STRENGTH, minutes="74"),
    )
    assert context.is_training_day is True
    assert context.qualifying_session_count == 2
    assert context.qualifying_duration_seconds == Decimal("10440")
    assert context.qualifying_session_ids == (UUID(int=1), UUID(int=2))
    assert context.reason_codes == (TrainingDayReasonCode.QUALIFYING_STRENGTH_SESSION,)
    assert tuple(item.source_name for item in context.source_summary) == ("Apple Watch", "Hevy")


def test_requested_timezone_and_start_time_control_local_day_attribution() -> None:
    session = _session(
        1,
        start=datetime(2026, 9, 4, 3, 30, tzinfo=UTC),
        minutes="60",
    )
    assert _context(session, local_date=date(2026, 9, 3)).is_training_day is True
    assert _context(session, local_date=date(2026, 9, 4)).is_training_day is False
    assert (
        _context(
            session,
            local_date=date(2026, 9, 4),
            timezone="UTC",
        ).is_training_day
        is True
    )


def test_midnight_crossing_workout_counts_only_for_start_date() -> None:
    session = _session(
        1,
        start=datetime(2026, 9, 4, 3, 55, tzinfo=UTC),
        minutes="30",
    )
    assert _context(session, local_date=date(2026, 9, 3)).is_training_day is True
    assert _context(session, local_date=date(2026, 9, 4)).reason_codes == (
        TrainingDayReasonCode.NO_SESSIONS,
    )


def test_missing_or_invalid_timezone_fails_closed() -> None:
    with pytest.raises(InvalidDailyHealthTimezone):
        _context(_session(1), timezone="")
    with pytest.raises(InvalidDailyHealthTimezone):
        _context(_session(1), timezone="Not/A_Zone")


def test_tombstoned_workout_is_excluded() -> None:
    session = _session(1, tombstoned=True)
    assert qualify_training_session(session).reason_code is TrainingDayReasonCode.TOMBSTONED_SESSION
    assert _context(session).reason_codes == (TrainingDayReasonCode.NO_SESSIONS,)


def test_active_energy_is_irrelevant_to_qualification() -> None:
    session = _session(1, energy=None)
    without_energy = _context(session)
    with_large_energy = _context(replace(session, active_energy_kcal=Decimal("99999")))
    assert without_energy == with_large_energy
    assert not {
        "active_energy_kcal",
        "calorie_target",
        "target_policy",
    } & set(without_energy.__dataclass_fields__)


def test_duplicate_source_identity_is_not_double_counted() -> None:
    first = _session(1, source_record_number=99, minutes="60")
    duplicate = _session(2, source_record_number=99, minutes="60")
    context = _context(first, duplicate)
    assert context.qualifying_session_count == 1
    assert context.qualifying_duration_seconds == Decimal("3600")
    assert len(context.qualifying_source_identities) == 1


def test_tombstone_wins_over_duplicate_active_identity() -> None:
    active = _session(1, source_record_number=99)
    tombstone = _session(2, source_record_number=99, tombstoned=True)
    assert _context(active, tombstone).is_training_day is False


def test_application_use_case_reads_owner_scope_and_preserves_policy_version() -> None:
    repository = InMemoryTrainingSessionRepository(clock=lambda: INGESTED_AT)
    repository.apply_batch(
        USER,
        TrainingSyncBatch(
            client_batch_id=UUID(int=90),
            added=(
                TrainingSessionObservation(
                    source_system=TrainingSourceSystem.HEALTHKIT,
                    source_record_id=str(UUID(int=91)),
                    activity_type=TRADITIONAL_STRENGTH,
                    started_at=datetime(2026, 9, 3, 14, tzinfo=UTC),
                    ended_at=datetime(2026, 9, 3, 15, tzinfo=UTC),
                    active_duration_seconds=Decimal("3600"),
                    active_energy_kcal=None,
                    timezone_identifier=None,
                    source_name="Hevy",
                    source_bundle_id="com.hevyapp.hevy",
                    source_revision=None,
                ),
            ),
            deletions=(),
        ),
    )
    context = TrainingDayContextUseCase(training=repository).execute(
        user_id=USER,
        local_date=DAY,
        timezone="America/New_York",
    )
    assert context.is_training_day is True
    assert context.policy_version == OWNER_STRENGTH_TRAINING_DAY_POLICY_V1.version


def test_normalization_does_not_consider_source_name_or_energy() -> None:
    original = _session(1, source_name="Hevy", energy="900")
    changed = replace(original, source_name="Other App", active_energy_kcal=None)
    assert normalized_workout_kind(original) == normalized_workout_kind(changed)
