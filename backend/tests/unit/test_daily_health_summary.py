"""Deterministic M12A daily health summary and factual training-day tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.daily_health import DailyHealthSummaryUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryTrainingSessionRepository,
)
from nutrition_agent.domain.health.entities import BodyMassSample, SyncBatch
from nutrition_agent.domain.training import (
    TrainingSessionObservation,
    TrainingSourceSystem,
    TrainingSyncBatch,
    has_readable_workout_on_local_date,
)

USER = UUID(int=1)
DAY = date(2026, 9, 3)


def _training(source_id: UUID, start: datetime, *, energy: str | None):
    return TrainingSessionObservation(
        source_system=TrainingSourceSystem.HEALTHKIT,
        source_record_id=str(source_id),
        activity_type="37",
        started_at=start,
        ended_at=start + timedelta(minutes=30),
        active_duration_seconds=Decimal("1800"),
        active_energy_kcal=Decimal(energy) if energy is not None else None,
        timezone_identifier=None,
        source_name="Watch",
        source_bundle_id="com.apple.health",
        source_revision=None,
    )


def test_local_date_summary_selects_latest_weight_and_sums_complete_workouts() -> None:
    body = InMemoryHealthBodyMassRepository()
    training = InMemoryTrainingSessionRepository(clock=lambda: datetime(2026, 9, 4, tzinfo=UTC))
    body.apply_batch(
        USER,
        SyncBatch(
            client_batch_id=UUID(int=20),
            added=(
                BodyMassSample(
                    UUID(int=1),
                    Decimal("70"),
                    datetime(2026, 9, 3, 5, tzinfo=UTC),
                    datetime(2026, 9, 3, 5, tzinfo=UTC),
                    None,
                    None,
                ),
                BodyMassSample(
                    UUID(int=2),
                    Decimal("69.8"),
                    datetime(2026, 9, 4, 3, tzinfo=UTC),
                    datetime(2026, 9, 4, 3, tzinfo=UTC),
                    None,
                    None,
                ),
            ),
            deletions=(),
        ),
    )
    training.apply_batch(
        USER,
        TrainingSyncBatch(
            UUID(int=21),
            (
                _training(UUID(int=3), datetime(2026, 9, 3, 12, tzinfo=UTC), energy="200"),
                _training(UUID(int=4), datetime(2026, 9, 4, 1, tzinfo=UTC), energy="150"),
            ),
            (),
        ),
    )

    summary = DailyHealthSummaryUseCase(body_mass=body, training=training).execute(
        user_id=USER, local_date=DAY, timezone="America/New_York"
    )

    assert summary.latest_body_mass is not None
    assert summary.latest_body_mass.sample_uuid == UUID(int=2)
    assert summary.workout_count == 2
    assert summary.total_workout_duration_seconds == Decimal("3600")
    assert summary.total_workout_active_energy_kcal == Decimal("350")
    assert len(summary.training_sources) == 2
    assert has_readable_workout_on_local_date(summary) is True


def test_no_observations_are_unknown_not_authoritative_zero() -> None:
    summary = DailyHealthSummaryUseCase(
        body_mass=InMemoryHealthBodyMassRepository(),
        training=InMemoryTrainingSessionRepository(),
    ).execute(user_id=USER, local_date=DAY, timezone="America/New_York")
    assert summary.latest_body_mass is None
    assert summary.workout_count is None
    assert summary.total_workout_duration_seconds is None
    assert summary.total_workout_active_energy_kcal is None
    assert summary.training_sources == ()
    assert has_readable_workout_on_local_date(summary) is False


def test_missing_one_energy_keeps_total_energy_unknown_not_partial_sum() -> None:
    training = InMemoryTrainingSessionRepository()
    training.apply_batch(
        USER,
        TrainingSyncBatch(
            UUID(int=30),
            (
                _training(UUID(int=1), datetime(2026, 9, 3, 12, tzinfo=UTC), energy="200"),
                _training(UUID(int=2), datetime(2026, 9, 3, 15, tzinfo=UTC), energy=None),
            ),
            (),
        ),
    )
    summary = DailyHealthSummaryUseCase(
        body_mass=InMemoryHealthBodyMassRepository(), training=training
    ).execute(user_id=USER, local_date=DAY, timezone="America/New_York")
    assert summary.workout_count == 2
    assert summary.total_workout_duration_seconds == Decimal("3600")
    assert summary.total_workout_active_energy_kcal is None
