"""Domain proofs for source-agnostic detailed training observations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from nutrition_agent.domain.training.detail import (
    DetailedTrainingSession,
    DetailedTrainingSet,
    DetailedTrainingSourceSystem,
    ExercisePerformance,
    TrainingDistance,
    TrainingDistanceUnit,
    TrainingLoad,
    TrainingLoadUnit,
    TrainingSetType,
)

T0 = datetime(2026, 9, 3, 14, tzinfo=UTC)


def _set(index: int = 0) -> DetailedTrainingSet:
    return DetailedTrainingSet(
        set_identity=f"0:{index}",
        set_index=index,
        set_type=TrainingSetType.NORMAL,
        reps=8,
        load=TrainingLoad(Decimal("65.000"), TrainingLoadUnit.KILOGRAM),
    )


def _exercise(order: int = 0, *, source_id: str = "template-bench") -> ExercisePerformance:
    return ExercisePerformance(
        occurrence_identity=f"{source_id}:{order}",
        source_exercise_id=source_id,
        display_name="Bench Press (Barbell)",
        exercise_order=order,
        sets=(_set(),),
    )


def _session() -> DetailedTrainingSession:
    return DetailedTrainingSession(
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_session_id="source-workout-1",
        source_revision="2026-09-03T15:00:00+00:00",
        title="Push Day",
        started_at=T0,
        ended_at=T0.replace(hour=15),
        exercises=(_exercise(),),
        parser_version="hevy-public-api.v1",
        source_payload_sha256="a" * 64,
    )


def test_valid_session_preserves_exact_source_identity_and_decimal_values() -> None:
    session = _session()
    training_set = session.exercises[0].sets[0]
    assert session.source_system is DetailedTrainingSourceSystem.HEVY
    assert session.source_session_id == "source-workout-1"
    assert training_set.load is not None
    assert training_set.load.value == Decimal("65.000")
    assert isinstance(training_set.load.value, Decimal)


def test_pounds_convert_to_kilograms_without_binary_float() -> None:
    load = TrainingLoad(Decimal("100"), TrainingLoadUnit.POUND)
    assert load.kilograms == Decimal("45.35923700")


def test_missing_load_and_reps_remain_unknown_not_zero() -> None:
    training_set = DetailedTrainingSet("0:0", 0, TrainingSetType.NORMAL)
    assert training_set.load is None
    assert training_set.reps is None


def test_signed_load_can_preserve_source_reported_assistance() -> None:
    load = TrainingLoad(Decimal("-20"), TrainingLoadUnit.KILOGRAM)
    assert load.value == Decimal("-20")


def test_distance_and_duration_require_decimal_and_nonnegative_values() -> None:
    assert TrainingDistance(Decimal("20.5"), TrainingDistanceUnit.METER).value == Decimal("20.5")
    with pytest.raises(TypeError):
        TrainingLoad(20.5, TrainingLoadUnit.KILOGRAM)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TrainingDistance(Decimal("-1"), TrainingDistanceUnit.METER)


def test_repeated_exercise_source_id_is_valid_when_occurrence_order_differs() -> None:
    first = _exercise(0)
    second = replace(
        _exercise(1),
        occurrence_identity="template-bench:1",
        sets=(replace(_set(), set_identity="1:0"),),
    )
    session = replace(_session(), exercises=(first, second))
    assert tuple(item.source_exercise_id for item in session.exercises) == (
        "template-bench",
        "template-bench",
    )


def test_display_name_is_metadata_not_exercise_identity() -> None:
    exercise = replace(_exercise(), display_name="Renamed by source")
    assert exercise.source_exercise_id == "template-bench"
    assert exercise.occurrence_identity == "template-bench:0"


def test_exercise_and_set_order_are_deterministic_and_validated() -> None:
    with pytest.raises(ValueError, match="sets must be ordered"):
        replace(_exercise(), sets=(_set(1), _set(0)))
    with pytest.raises(ValueError, match="exercises must be ordered"):
        replace(_session(), exercises=(_exercise(1), _exercise(0)))


def test_session_requires_aware_ordered_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(_session(), started_at=datetime(2026, 9, 3, 14))
    with pytest.raises(ValueError, match="precedes"):
        replace(_session(), ended_at=T0.replace(hour=13))
