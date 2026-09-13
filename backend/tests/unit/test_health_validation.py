"""Domain tests for body-mass batch validation (fail-closed semantics)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from nutrition_agent.domain.health.validation import (
    MAX_DECIMAL_PLACES,
    SKEW_TOLERANCE,
    BatchRejected,
    validate_body_mass_batch,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _added(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sample_uuid": "00000000-0000-0000-0000-000000000001",
        "value": "68.039",
        "sample_start": "2026-08-21T07:12:00+00:00",
        "sample_end": "2026-08-21T07:12:00+00:00",
    }
    base.update(overrides)
    return base


def _validate(added: list[dict[str, object]] | None = None, deleted=None):
    return validate_body_mass_batch(
        str(uuid4()),
        added if added is not None else [_added()],
        deleted if deleted is not None else [],
        now=NOW,
    )


def test_valid_sample_parses_to_exact_decimal() -> None:
    batch = _validate()
    assert len(batch.added) == 1
    sample = batch.added[0]
    assert str(sample.value_kg) == "68.039"
    assert sample.sample_start.tzinfo is UTC


def test_optional_source_fields_roundtrip() -> None:
    batch = _validate([_added(source_name="Smart Scale", source_bundle_id="com.vendor.scale")])
    assert batch.added[0].source_name == "Smart Scale"
    assert batch.added[0].source_bundle_id == "com.vendor.scale"


def test_blank_source_text_becomes_none() -> None:
    batch = _validate([_added(source_name="   ")])
    assert batch.added[0].source_name is None


def test_json_float_value_is_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(value=68.039)])


def test_more_than_three_decimal_places_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(value="68.0385")])
    assert MAX_DECIMAL_PLACES == 3


def test_non_numeric_value_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(value="abc")])


@pytest.mark.parametrize("value", ["19.999", "400.001", "-70", "0"])
def test_out_of_bounds_values_rejected(value: str) -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(value=value)])


def test_boundary_values_accepted() -> None:
    assert _validate([_added(value="20")]).added[0].value_kg == Decimal(20)
    assert _validate([_added(value="400.000")]).added[0].value_kg == Decimal("400.000")


def test_end_before_start_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate(
            [
                _added(
                    sample_start="2026-08-21T08:00:00+00:00",
                    sample_end="2026-08-21T07:00:00+00:00",
                )
            ]
        )


def test_future_timestamp_beyond_skew_rejected() -> None:
    too_late = NOW + SKEW_TOLERANCE + timedelta(seconds=1)
    with pytest.raises(BatchRejected):
        _validate(
            [
                _added(
                    sample_start=too_late.isoformat(),
                    sample_end=too_late.isoformat(),
                )
            ]
        )


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(sample_start="2026-08-21T07:12:00")])


def test_non_canonical_uuid_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(sample_uuid="00000000-0000-0000-0000-0000000001A")])


def test_duplicate_added_uuids_rejected() -> None:
    with pytest.raises(BatchRejected) as excinfo:
        _validate([_added(), _added()])
    assert any("duplicate" in e.reason for e in excinfo.value.errors)


def test_duplicate_deleted_uuids_rejected() -> None:
    dup = {"sample_uuid": "00000000-0000-0000-0000-000000000002"}
    with pytest.raises(BatchRejected):
        _validate(deleted=[dup, dup])


def test_unsupported_metric_rejected() -> None:
    with pytest.raises(BatchRejected):
        _validate([_added(metric="body_fat_percentage")])


def test_body_mass_metric_explicitly_accepted() -> None:
    batch = _validate([_added(metric="body_mass")])
    assert len(batch.added) == 1


def test_oversize_batches_rejected() -> None:
    big = [
        {
            "sample_uuid": f"00000000-0000-0000-0000-{i:012x}",
            "value": "70",
            "sample_start": "2026-08-21T07:12:00+00:00",
            "sample_end": "2026-08-21T07:12:00+00:00",
        }
        for i in range(501)
    ]
    with pytest.raises(BatchRejected) as excinfo:
        _validate(added=big)
    assert "exceeds" in excinfo.value.errors[0].reason


def test_malformed_containers_rejected() -> None:
    for bad_added in ("nope", [{"sample_uuid": "x"}], [42]):
        with pytest.raises(BatchRejected):
            _validate(added=bad_added)
    with pytest.raises(BatchRejected):
        validate_body_mass_batch(str(uuid4()), [_added()], "nope", now=NOW)
    with pytest.raises(BatchRejected):
        validate_body_mass_batch("not-a-uuid", [], [], now=NOW)


def test_all_or_nothing_single_error_kills_whole_batch() -> None:
    good_second = {
        "sample_uuid": "00000000-0000-0000-0000-000000000003",
        "value": "70.5",
        "sample_start": "2026-08-21T07:12:00+00:00",
        "sample_end": "2026-08-21T07:12:00+00:00",
    }
    with pytest.raises(BatchRejected):
        _validate([_added(value="999"), good_second])
