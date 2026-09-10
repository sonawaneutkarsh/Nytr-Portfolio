"""All-or-nothing validation for body-mass sync batches (fail-closed).

Pure domain function: caller supplies ``now`` (no hidden clocks — house rule).
Any rejection aborts the whole batch; nothing partial is ever accepted
(mirrors ADR-013 fail-closed ingestion posture).

Rules (ADR-016 / docs/APPLE_HEALTH.md):
- canonical unit is kilograms, transported as decimal strings (JSON floats are
  rejected outright so no binary-float value can reach health data);
- at most 3 decimal places (protects the numeric(6,3) column from silent
  rounding);
- physiologic bounds 20–400 kg inclusive;
- sample_end >= sample_start;
- sample_end <= now + SKEW_TOLERANCE (device-clock guard);
- tz-aware timestamps required;
- no duplicate sample UUIDs within added or within deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from nutrition_agent.domain.health.entities import (
    METRIC_BODY_MASS,
    BodyMassSample,
    SampleDeletion,
    SyncBatch,
)

MIN_VALUE_KG = Decimal("20")
MAX_VALUE_KG = Decimal("400")
SKEW_TOLERANCE = timedelta(minutes=5)
MAX_DECIMAL_PLACES = 3

_ADDED_LIMIT = 500
_DELETED_LIMIT = 500


class BatchRejected(Exception):
    """Domain-level rejection: the whole batch must be refused."""

    def __init__(self, errors: tuple[RejectedField, ...]) -> None:
        self.errors = errors
        detail = "; ".join(f"{e.field}: {e.reason}" for e in errors)
        super().__init__(f"batch rejected ({len(errors)} error(s)): {detail}")


@dataclass(frozen=True)
class RejectedField:
    field: str
    reason: str


def _parse_kg(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("value must be a decimal string")
    try:
        kg = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("value is not a valid decimal") from exc
    if not kg.is_finite() or kg <= 0:
        raise ValueError("value must be a positive finite decimal")
    places = -int(kg.as_tuple().exponent)
    if places > MAX_DECIMAL_PLACES:
        raise ValueError(f"value exceeds {MAX_DECIMAL_PLACES} decimal places")
    return kg


def _require_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a uuid string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid uuid") from exc
    if str(parsed) != value.lower():
        raise ValueError(f"{label} must be canonical lowercase hyphenated form")
    return parsed


def _require_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _optional_text(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    stripped = value.strip()
    if len(stripped) > 200:
        raise ValueError(f"{key} exceeds 200 characters")
    return stripped or None


def validate_body_mass_batch(
    raw_batch_id: object,
    raw_added: object,
    raw_deleted: object,
    *,
    now: datetime,
) -> SyncBatch:
    """Validate one raw sync payload into a SyncBatch; reject all-or-nothing."""
    try:
        batch_id = _require_uuid(raw_batch_id, "client_batch_id")
    except ValueError as exc:
        raise BatchRejected((RejectedField("client_batch_id", str(exc)),)) from exc

    if not isinstance(raw_added, list):
        raise BatchRejected((RejectedField("added", "must be a list"),))
    if not isinstance(raw_deleted, list):
        raise BatchRejected((RejectedField("deleted", "must be a list"),))
    if len(raw_added) > _ADDED_LIMIT:
        raise BatchRejected((RejectedField("added", f"exceeds {_ADDED_LIMIT} items"),))
    if len(raw_deleted) > _DELETED_LIMIT:
        raise BatchRejected((RejectedField("deleted", f"exceeds {_DELETED_LIMIT} items"),))

    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    max_end = now_utc + SKEW_TOLERANCE

    errors: list[RejectedField] = []
    added: list[BodyMassSample] = []
    seen_add: set[str] = set()

    for index, item in enumerate(raw_added):
        prefix = f"added[{index}]"
        if not isinstance(item, dict):
            errors.append(RejectedField(prefix, "must be an object"))
            continue
        try:
            sample_uuid = _require_uuid(item.get("sample_uuid"), f"{prefix}.sample_uuid")
            value_kg = _parse_kg(item.get("value"))
            start = _require_datetime(item.get("sample_start"), f"{prefix}.sample_start")
            end = _require_datetime(item.get("sample_end"), f"{prefix}.sample_end")
            source_name = _optional_text(item, "source_name")
            source_bundle = _optional_text(item, "source_bundle_id")
            metric = item.get("metric")
            if metric is not None and metric != METRIC_BODY_MASS:
                raise ValueError(f"unsupported metric {metric!r}")
            if end < start:
                raise ValueError("sample_end precedes sample_start")
            if end > max_end:
                raise ValueError("sample_end is in the future beyond skew tolerance")
            if not MIN_VALUE_KG <= value_kg <= MAX_VALUE_KG:
                raise ValueError(f"value outside [{MIN_VALUE_KG}, {MAX_VALUE_KG}] kg")
        except ValueError as exc:
            errors.append(RejectedField(prefix, str(exc)))
            continue
        key = str(sample_uuid)
        if key in seen_add:
            errors.append(RejectedField(prefix, f"duplicate sample_uuid {key}"))
            continue
        seen_add.add(key)
        added.append(
            BodyMassSample(
                sample_uuid=sample_uuid,
                value_kg=value_kg,
                sample_start=start,
                sample_end=end,
                source_name=source_name,
                source_bundle_id=source_bundle,
            )
        )

    deleted: list[SampleDeletion] = []
    seen_del: set[str] = set()

    for index, item in enumerate(raw_deleted):
        prefix = f"deleted[{index}]"
        if not isinstance(item, dict):
            errors.append(RejectedField(prefix, "must be an object"))
            continue
        try:
            sample_uuid = _require_uuid(item.get("sample_uuid"), f"{prefix}.sample_uuid")
        except ValueError as exc:
            errors.append(RejectedField(prefix, str(exc)))
            continue
        key = str(sample_uuid)
        if key in seen_del:
            errors.append(RejectedField(prefix, f"duplicate sample_uuid {key}"))
            continue
        seen_del.add(key)
        deleted.append(SampleDeletion(sample_uuid=sample_uuid))

    if errors:
        raise BatchRejected(tuple(errors))
    return SyncBatch(client_batch_id=batch_id, added=tuple(added), deletions=tuple(deleted))
