"""Deterministic parser and offline fixtures for the official Hevy schema."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from nutrition_agent.application.ports import DetailedTrainingSource
from nutrition_agent.domain.training.detail import (
    DetailedTrainingDeletion,
    DetailedTrainingImportBatch,
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

HEVY_PUBLIC_API_PARSER_VERSION = "hevy-public-api.v1"


class HevySourceFailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED_PAYLOAD = "malformed_payload"
    UNSUPPORTED_RECORD = "unsupported_record"
    INCOMPLETE_PAGINATION = "incomplete_pagination"


class HevySourceFailure(RuntimeError):
    def __init__(self, kind: HevySourceFailureKind, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


@dataclass(frozen=True)
class ParsedHevyEventPage:
    page: int
    page_count: int
    sessions: tuple[DetailedTrainingSession, ...]
    deletions: tuple[DetailedTrainingDeletion, ...]
    event_times: tuple[datetime, ...]


@dataclass(frozen=True)
class ParsedHevyWorkoutPage:
    page: int
    page_count: int
    sessions: tuple[DetailedTrainingSession, ...]


@dataclass(frozen=True)
class HevyFixtureProvider(DetailedTrainingSource):
    """Bounded, network-free provider for sanitized official-schema fixtures."""

    page_payloads: tuple[bytes, ...]
    max_pages: int = 100

    def load_changes(self, since: datetime | None) -> DetailedTrainingImportBatch:
        del since  # A future authenticated transport owns query construction.
        if not self.page_payloads:
            raise HevySourceFailure(
                HevySourceFailureKind.INCOMPLETE_PAGINATION,
                "fixture provider requires at least one page",
            )
        if len(self.page_payloads) > self.max_pages:
            raise HevySourceFailure(
                HevySourceFailureKind.INCOMPLETE_PAGINATION,
                "fixture page count exceeds configured bound",
            )
        pages = tuple(parse_hevy_event_page(payload) for payload in self.page_payloads)
        expected_count = pages[0].page_count
        if expected_count > self.max_pages or expected_count != len(pages):
            raise HevySourceFailure(
                HevySourceFailureKind.INCOMPLETE_PAGINATION,
                "Hevy pagination ended before the advertised page count",
            )
        if any(page.page_count != expected_count for page in pages) or tuple(
            page.page for page in pages
        ) != tuple(range(1, expected_count + 1)):
            raise HevySourceFailure(
                HevySourceFailureKind.INCOMPLETE_PAGINATION,
                "Hevy pagination metadata is inconsistent",
            )
        sessions = tuple(session for page in pages for session in page.sessions)
        deletions = tuple(deletion for page in pages for deletion in page.deletions)
        try:
            return DetailedTrainingImportBatch(
                sessions=sessions,
                deletions=deletions,
                complete=True,
            )
        except ValueError as exc:
            raise HevySourceFailure(
                HevySourceFailureKind.UNSUPPORTED_RECORD,
                "Hevy page sequence repeats one revision or deletion identity",
            ) from exc


def parse_hevy_event_page(payload: bytes) -> ParsedHevyEventPage:
    try:
        raw = json.loads(payload, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HevySourceFailure(
            HevySourceFailureKind.MALFORMED_PAYLOAD, "Hevy response is not valid JSON"
        ) from exc
    if not isinstance(raw, dict):
        raise _malformed("Hevy page must be an object")
    page = _required_int(raw, "page")
    page_count = _required_int(raw, "page_count")
    if page < 1 or page_count < 1 or page > page_count:
        raise _malformed("Hevy pagination values are invalid")
    events = raw.get("events")
    if not isinstance(events, list):
        raise _malformed("Hevy page events must be an array")
    sessions: list[DetailedTrainingSession] = []
    deletions: list[DetailedTrainingDeletion] = []
    event_times: list[datetime] = []
    for event in events:
        if not isinstance(event, dict):
            raise _malformed("Hevy event must be an object")
        event_type = event.get("type")
        if event_type == "updated":
            workout = event.get("workout")
            if not isinstance(workout, dict):
                raise _malformed("updated event requires a workout object")
            session = _parse_workout(workout, _semantic_sha256(workout))
            sessions.append(session)
            assert session.source_updated_at is not None
            event_times.append(session.source_updated_at)
        elif event_type == "deleted":
            removed_at = _required_datetime(event, "deleted_at")
            deletions.append(
                DetailedTrainingDeletion(
                    source_system=DetailedTrainingSourceSystem.HEVY,
                    source_session_id=_required_text(event, "id"),
                    removed_at=removed_at,
                    parser_version=HEVY_PUBLIC_API_PARSER_VERSION,
                    source_payload_sha256=_semantic_sha256(event),
                )
            )
            event_times.append(removed_at)
        else:
            raise HevySourceFailure(
                HevySourceFailureKind.UNSUPPORTED_RECORD,
                "Hevy event type is not supported",
            )
    return ParsedHevyEventPage(
        page,
        page_count,
        tuple(sessions),
        tuple(deletions),
        tuple(event_times),
    )


def parse_hevy_workout_page(payload: bytes) -> ParsedHevyWorkoutPage:
    """Parse one official ``GET /v1/workouts`` response without floats."""

    try:
        raw = json.loads(payload, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HevySourceFailure(
            HevySourceFailureKind.MALFORMED_PAYLOAD,
            "Hevy response is not valid JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise _malformed("Hevy page must be an object")
    page = _required_int(raw, "page")
    page_count = _required_int(raw, "page_count")
    if page < 1 or page_count < 1 or page > page_count:
        raise _malformed("Hevy pagination values are invalid")
    workouts = raw.get("workouts")
    if not isinstance(workouts, list):
        raise _malformed("Hevy page workouts must be an array")
    sessions: list[DetailedTrainingSession] = []
    for workout in workouts:
        if not isinstance(workout, dict):
            raise _malformed("Hevy workout must be an object")
        sessions.append(_parse_workout(workout, _semantic_sha256(workout)))
    return ParsedHevyWorkoutPage(page, page_count, tuple(sessions))


def _parse_workout(raw: dict[str, Any], digest: str) -> DetailedTrainingSession:
    source_updated_at = _required_datetime(raw, "updated_at")
    exercises_raw = raw.get("exercises")
    if not isinstance(exercises_raw, list):
        raise _malformed("Hevy workout exercises must be an array")
    exercises = tuple(
        sorted(
            (_parse_exercise(item) for item in exercises_raw),
            key=lambda item: item.exercise_order,
        )
    )
    if len({item.exercise_order for item in exercises}) != len(exercises):
        raise _malformed("Hevy exercise indexes must be unique")
    return DetailedTrainingSession(
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_session_id=_required_text(raw, "id"),
        source_revision=source_updated_at.isoformat(),
        title=_required_text(raw, "title"),
        description=_optional_text(raw, "description"),
        routine_id=_optional_text(raw, "routine_id"),
        started_at=_required_datetime(raw, "start_time"),
        ended_at=_required_datetime(raw, "end_time"),
        source_created_at=_required_datetime(raw, "created_at"),
        source_updated_at=source_updated_at,
        exercises=exercises,
        parser_version=HEVY_PUBLIC_API_PARSER_VERSION,
        source_payload_sha256=digest,
    )


def _parse_exercise(raw: object) -> ExercisePerformance:
    if not isinstance(raw, dict):
        raise _malformed("Hevy exercise must be an object")
    exercise_order = _required_int(raw, "index")
    source_id = _required_text(raw, "exercise_template_id")
    sets_raw = raw.get("sets")
    if not isinstance(sets_raw, list):
        raise _malformed("Hevy exercise sets must be an array")
    sets = tuple(
        sorted((_parse_set(item, exercise_order) for item in sets_raw), key=lambda x: x.set_index)
    )
    if len({item.set_index for item in sets}) != len(sets):
        raise _malformed("Hevy set indexes must be unique within an exercise occurrence")
    superset_id = _optional_int(raw, "superset_id")
    return ExercisePerformance(
        occurrence_identity=f"{source_id}:{exercise_order}",
        source_exercise_id=source_id,
        display_name=_required_text(raw, "title"),
        exercise_order=exercise_order,
        notes=_optional_text(raw, "notes"),
        superset_id=superset_id,
        sets=sets,
    )


def _parse_set(raw: object, exercise_order: int) -> DetailedTrainingSet:
    if not isinstance(raw, dict):
        raise _malformed("Hevy set must be an object")
    set_index = _required_int(raw, "index")
    raw_type = _required_text(raw, "type")
    try:
        set_type = TrainingSetType(raw_type)
    except ValueError as exc:
        raise HevySourceFailure(
            HevySourceFailureKind.UNSUPPORTED_RECORD,
            "Hevy set type is not supported",
        ) from exc
    weight = _optional_decimal(raw, "weight_kg")
    distance = _optional_decimal(raw, "distance_meters", nonnegative=True)
    return DetailedTrainingSet(
        set_identity=f"{exercise_order}:{set_index}",
        set_index=set_index,
        set_type=set_type,
        reps=_optional_int(raw, "reps"),
        load=(TrainingLoad(weight, TrainingLoadUnit.KILOGRAM) if weight is not None else None),
        distance=(
            TrainingDistance(distance, TrainingDistanceUnit.METER) if distance is not None else None
        ),
        duration_seconds=_optional_decimal(raw, "duration_seconds", nonnegative=True),
        rpe=_optional_decimal(raw, "rpe", nonnegative=True),
        custom_metric=_optional_decimal(raw, "custom_metric", nonnegative=True),
    )


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _malformed(f"Hevy field {key} must be non-empty text")
    return value


def _optional_text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _malformed(f"Hevy field {key} must be text or null")
    return value or None


def _required_int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _malformed(f"Hevy field {key} must be an integer")
    return value


def _optional_int(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _malformed(f"Hevy field {key} must be a nonnegative integer or null")
    return value


def _optional_decimal(
    raw: dict[str, Any], key: str, *, nonnegative: bool = False
) -> Decimal | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise _malformed(f"Hevy field {key} must be a number or null")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite() or (nonnegative and result < 0):
        raise _malformed(f"Hevy field {key} is outside the supported range")
    return result


def _required_datetime(raw: dict[str, Any], key: str) -> datetime:
    value = _required_text(raw, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _malformed(f"Hevy field {key} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _malformed(f"Hevy field {key} must include an offset")
    return parsed


def _semantic_sha256(value: object) -> str:
    canonical = json.dumps(
        _canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize(value: object) -> object:
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return {"$decimal": format(normalized, "f")}
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def _malformed(detail: str) -> HevySourceFailure:
    return HevySourceFailure(HevySourceFailureKind.MALFORMED_PAYLOAD, detail)


__all__ = [
    "HEVY_PUBLIC_API_PARSER_VERSION",
    "HevyFixtureProvider",
    "HevySourceFailure",
    "HevySourceFailureKind",
    "ParsedHevyEventPage",
    "ParsedHevyWorkoutPage",
    "parse_hevy_event_page",
    "parse_hevy_workout_page",
]
