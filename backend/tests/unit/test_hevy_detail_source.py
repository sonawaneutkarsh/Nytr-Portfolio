"""Offline fixture tests for the documented Hevy public event schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nutrition_agent.domain.training.detail import TrainingSetType
from nutrition_agent.infrastructure.hevy_detail_source import (
    HEVY_PUBLIC_API_PARSER_VERSION,
    HevyFixtureProvider,
    HevySourceFailure,
    HevySourceFailureKind,
    parse_hevy_event_page,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hevy"


def _payload(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_paginated_fixture_parses_complete_detailed_sessions_and_deletion() -> None:
    batch = HevyFixtureProvider(
        (_payload("events_page_1.json"), _payload("events_page_2.json"))
    ).load_changes(None)
    assert batch.complete is True
    assert tuple(item.title for item in batch.sessions) == ("Push Day", "Lower Day")
    assert tuple(item.source_session_id for item in batch.deletions) == (
        "fixture-workout-deleted-1",
    )
    push = batch.sessions[0]
    assert push.parser_version == HEVY_PUBLIC_API_PARSER_VERSION
    assert len(push.source_payload_sha256) == 64
    assert len(push.exercises) == 6
    assert push.exercises[0].source_exercise_id == push.exercises[2].source_exercise_id
    assert push.exercises[0].occurrence_identity != push.exercises[2].occurrence_identity
    assert tuple(item.set_type for item in push.exercises[0].sets) == (
        TrainingSetType.WARMUP,
        TrainingSetType.NORMAL,
        TrainingSetType.FAILURE,
    )


def test_fixture_preserves_notes_bodyweight_assistance_and_unknown_values() -> None:
    page = parse_hevy_event_page(_payload("events_page_1.json"))
    push = page.sessions[0]
    assert push.exercises[0].notes == "Controlled pause"
    assert push.exercises[3].sets[0].load is None
    assert push.exercises[3].sets[0].reps == 8
    assistance = push.exercises[4].sets[0].load
    assert assistance is not None and str(assistance.value) == "-20"
    plank = push.exercises[5].sets[0]
    assert plank.reps is None and str(plank.duration_seconds) == "60"


def test_distance_custom_metric_and_missing_reps_are_preserved() -> None:
    page = parse_hevy_event_page(_payload("events_page_2.json"))
    sets = page.sessions[0].exercises[0].sets
    assert str(sets[0].distance.value) == "20.5"  # type: ignore[union-attr]
    assert sets[0].load is None
    assert sets[1].reps is None
    assert str(sets[1].custom_metric) == "30"


def test_source_update_is_a_new_explicit_revision() -> None:
    original = parse_hevy_event_page(_payload("events_page_1.json")).sessions[0]
    updated = parse_hevy_event_page(_payload("events_updated_revision.json")).sessions[0]
    assert original.source_session_id == updated.source_session_id
    assert original.source_revision != updated.source_revision
    assert original.source_payload_sha256 != updated.source_payload_sha256


def test_same_fixture_replay_has_deterministic_revision_and_digest() -> None:
    first = parse_hevy_event_page(_payload("events_page_1.json")).sessions[0]
    second = parse_hevy_event_page(_payload("events_page_1.json")).sessions[0]
    assert first == second


def test_incomplete_or_inconsistent_pagination_fails_closed() -> None:
    provider = HevyFixtureProvider((_payload("events_page_1.json"),))
    with pytest.raises(HevySourceFailure) as error:
        provider.load_changes(None)
    assert error.value.kind is HevySourceFailureKind.INCOMPLETE_PAGINATION


def test_malformed_json_and_unsupported_set_type_are_distinct_failures() -> None:
    with pytest.raises(HevySourceFailure) as malformed:
        parse_hevy_event_page(b"not-json")
    assert malformed.value.kind is HevySourceFailureKind.MALFORMED_PAYLOAD

    raw = json.loads(_payload("events_updated_revision.json"))
    raw["events"][0]["workout"]["exercises"][0]["sets"][0]["type"] = "mystery"
    with pytest.raises(HevySourceFailure) as unsupported:
        parse_hevy_event_page(json.dumps(raw).encode())
    assert unsupported.value.kind is HevySourceFailureKind.UNSUPPORTED_RECORD


def test_parser_tolerates_additive_unknown_fields_but_not_unknown_event_states() -> None:
    raw = json.loads(_payload("events_updated_revision.json"))
    raw["future_additive_field"] = "ignored"
    assert parse_hevy_event_page(json.dumps(raw).encode()).sessions
    raw["events"][0]["type"] = "created"
    with pytest.raises(HevySourceFailure) as unsupported:
        parse_hevy_event_page(json.dumps(raw).encode())
    assert unsupported.value.kind is HevySourceFailureKind.UNSUPPORTED_RECORD
