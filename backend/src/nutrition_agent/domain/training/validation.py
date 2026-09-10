"""Fail-closed validation for authenticated HealthKit workout batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nutrition_agent.domain.training import (
    TrainingSessionDeletion,
    TrainingSessionObservation,
    TrainingSourceSystem,
    TrainingSyncBatch,
)

SKEW_TOLERANCE = timedelta(minutes=5)
MAX_ADDED = 500
MAX_DELETED = 500
MAX_DECIMAL_TEXT_LENGTH = 64


@dataclass(frozen=True)
class TrainingRejectedField:
    field: str
    reason: str


class TrainingBatchRejected(Exception):
    def __init__(self, errors: tuple[TrainingRejectedField, ...]) -> None:
        self.errors = errors
        super().__init__("training batch rejected")


def _uuid(value: object, label: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a uuid string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid uuid") from exc
    if str(parsed) != value.lower():
        raise ValueError(f"{label} must be canonical lowercase hyphenated form")
    return parsed


def _source(value: object) -> TrainingSourceSystem:
    if value != TrainingSourceSystem.HEALTHKIT.value:
        raise ValueError("source_system must be 'healthkit'")
    return TrainingSourceSystem.HEALTHKIT


def _source_record_id(value: object, label: str) -> str:
    parsed = _uuid(value, label)
    return str(parsed)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _decimal(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a decimal string or null")
    if len(value) > MAX_DECIMAL_TEXT_LENGTH:
        raise ValueError(f"{label} is too long")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} is not a valid decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{label} must be a nonnegative finite decimal")
    return parsed


def _optional_text(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    normalized = value.strip()
    if len(normalized) > 200:
        raise ValueError(f"{key} exceeds 200 characters")
    return normalized or None


def _optional_timezone(raw: dict[str, object]) -> str | None:
    value = _optional_text(raw, "timezone_identifier")
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone_identifier is not recognized") from exc
    return value


def validate_training_batch(
    raw_batch_id: object,
    raw_added: object,
    raw_deleted: object,
    *,
    now: datetime,
) -> TrainingSyncBatch:
    """Validate one raw workout batch; any error rejects the whole batch."""

    try:
        batch_id = _uuid(raw_batch_id, "client_batch_id")
    except ValueError as exc:
        raise TrainingBatchRejected((TrainingRejectedField("client_batch_id", str(exc)),)) from exc
    if not isinstance(raw_added, list):
        raise TrainingBatchRejected((TrainingRejectedField("added", "must be a list"),))
    if not isinstance(raw_deleted, list):
        raise TrainingBatchRejected((TrainingRejectedField("deleted", "must be a list"),))
    if len(raw_added) > MAX_ADDED:
        raise TrainingBatchRejected((TrainingRejectedField("added", f"exceeds {MAX_ADDED} items"),))
    if len(raw_deleted) > MAX_DELETED:
        raise TrainingBatchRejected(
            (TrainingRejectedField("deleted", f"exceeds {MAX_DELETED} items"),)
        )

    max_end = (now if now.tzinfo is not None else now.replace(tzinfo=UTC)) + SKEW_TOLERANCE
    errors: list[TrainingRejectedField] = []
    added: list[TrainingSessionObservation] = []
    seen_added: set[tuple[TrainingSourceSystem, str]] = set()
    for index, item in enumerate(raw_added):
        prefix = f"added[{index}]"
        if not isinstance(item, dict):
            errors.append(TrainingRejectedField(prefix, "must be an object"))
            continue
        try:
            source = _source(item.get("source_system"))
            source_id = _source_record_id(
                item.get("source_record_id"), f"{prefix}.source_record_id"
            )
            started_at = _timestamp(item.get("started_at"), f"{prefix}.started_at")
            ended_at = _timestamp(item.get("ended_at"), f"{prefix}.ended_at")
            if ended_at < started_at:
                raise ValueError("ended_at precedes started_at")
            if ended_at > max_end:
                raise ValueError("ended_at is in the future beyond skew tolerance")
            observation = TrainingSessionObservation(
                source_system=source,
                source_record_id=source_id,
                activity_type=_optional_text(item, "activity_type"),
                started_at=started_at,
                ended_at=ended_at,
                active_duration_seconds=_decimal(
                    item.get("active_duration_seconds"), "active_duration_seconds"
                ),
                active_energy_kcal=_decimal(item.get("active_energy_kcal"), "active_energy_kcal"),
                timezone_identifier=_optional_timezone(item),
                source_name=_optional_text(item, "source_name"),
                source_bundle_id=_optional_text(item, "source_bundle_id"),
                source_revision=_optional_text(item, "source_revision"),
            )
            identity = (source, source_id)
            if identity in seen_added:
                raise ValueError("duplicate source identity")
            seen_added.add(identity)
            added.append(observation)
        except (TypeError, ValueError) as exc:
            errors.append(TrainingRejectedField(prefix, str(exc)))

    deletions: list[TrainingSessionDeletion] = []
    seen_deleted: set[tuple[TrainingSourceSystem, str]] = set()
    for index, item in enumerate(raw_deleted):
        prefix = f"deleted[{index}]"
        if not isinstance(item, dict):
            errors.append(TrainingRejectedField(prefix, "must be an object"))
            continue
        try:
            source = _source(item.get("source_system"))
            source_id = _source_record_id(
                item.get("source_record_id"), f"{prefix}.source_record_id"
            )
            identity = (source, source_id)
            if identity in seen_deleted:
                raise ValueError("duplicate source identity")
            seen_deleted.add(identity)
            deletions.append(TrainingSessionDeletion(source, source_id))
        except ValueError as exc:
            errors.append(TrainingRejectedField(prefix, str(exc)))

    if errors:
        raise TrainingBatchRejected(tuple(errors))
    return TrainingSyncBatch(batch_id, tuple(added), tuple(deletions))


__all__ = [
    "MAX_ADDED",
    "MAX_DELETED",
    "SKEW_TOLERANCE",
    "TrainingBatchRejected",
    "TrainingRejectedField",
    "validate_training_batch",
]
