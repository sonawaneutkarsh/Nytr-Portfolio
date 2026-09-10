"""Fail-closed domain validation for M12A workout ingestion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from nutrition_agent.domain.training import TrainingSourceSystem
from nutrition_agent.domain.training.validation import (
    SKEW_TOLERANCE,
    TrainingBatchRejected,
    validate_training_batch,
)

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _workout(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "source_system": "healthkit",
        "source_record_id": "00000000-0000-0000-0000-000000000001",
        "activity_type": "37",
        "started_at": "2026-09-03T10:00:00+00:00",
        "ended_at": "2026-09-03T11:00:00+00:00",
        "active_duration_seconds": "3600.000",
        "active_energy_kcal": "412.750",
        "timezone_identifier": "America/New_York",
        "source_name": "Apple Watch",
        "source_bundle_id": "com.apple.health",
        "source_revision": "26.0",
    }
    raw.update(overrides)
    return raw


def _validate(added: object = None, deleted: object = None):
    return validate_training_batch(
        str(uuid4()),
        [_workout()] if added is None else added,
        [] if deleted is None else deleted,
        now=NOW,
    )


def test_valid_workout_preserves_exact_decimals_and_provenance() -> None:
    observation = _validate().added[0]
    assert observation.source_system is TrainingSourceSystem.HEALTHKIT
    assert observation.active_duration_seconds == Decimal("3600.000")
    assert observation.active_energy_kcal == Decimal("412.750")
    assert observation.timezone_identifier == "America/New_York"
    assert observation.source_name == "Apple Watch"
    assert observation.source_bundle_id == "com.apple.health"
    assert observation.source_revision == "26.0"


def test_missing_optional_energy_timezone_and_source_metadata_remain_none() -> None:
    observation = _validate(
        [
            _workout(
                active_energy_kcal=None,
                timezone_identifier=None,
                source_name=None,
                source_bundle_id=None,
                source_revision=None,
            )
        ]
    ).added[0]
    assert observation.active_energy_kcal is None
    assert observation.timezone_identifier is None
    assert observation.source_name is None
    assert observation.source_bundle_id is None
    assert observation.source_revision is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active_duration_seconds", -1),
        ("active_duration_seconds", "-1"),
        ("active_energy_kcal", 1.25),
        ("active_energy_kcal", "NaN"),
    ],
)
def test_numeric_fields_are_nonnegative_decimal_strings_or_null(field: str, value: object) -> None:
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(**{field: value})])


def test_time_order_future_guard_and_timezone_awareness_fail_closed() -> None:
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(ended_at="2026-09-03T09:59:59+00:00")])
    future = NOW + SKEW_TOLERANCE + timedelta(seconds=1)
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(ended_at=future.isoformat())])
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(started_at="2026-09-03T10:00:00")])


def test_only_canonical_healthkit_source_identity_is_accepted() -> None:
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(source_system="hevy")])
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(source_record_id="NOT-A-UUID")])


def test_invalid_timezone_is_rejected_but_absence_is_not_invented() -> None:
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(timezone_identifier="Mars/Olympus")])
    assert _validate([_workout(timezone_identifier=None)]).added[0].timezone_identifier is None


def test_duplicate_add_and_delete_identities_are_rejected_all_or_nothing() -> None:
    with pytest.raises(TrainingBatchRejected):
        _validate([_workout(), _workout()])
    deletion = {
        "source_system": "healthkit",
        "source_record_id": "00000000-0000-0000-0000-000000000001",
    }
    with pytest.raises(TrainingBatchRejected):
        _validate([], [deletion, deletion])


def test_same_identity_in_add_and_delete_is_legal_and_deletion_wins_in_repository() -> None:
    deletion = {
        "source_system": "healthkit",
        "source_record_id": "00000000-0000-0000-0000-000000000001",
    }
    batch = _validate([_workout()], [deletion])
    assert len(batch.added) == 1 and len(batch.deletions) == 1
