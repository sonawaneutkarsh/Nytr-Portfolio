"""PostgreSQL proofs for bounded latest-active M14C analytics reads."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.db.sql_repos import SqlDetailedTrainingRepository
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingSourceSystem,
)
from nutrition_agent.infrastructure.hevy_detail_source import HevyFixtureProvider
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; requires scratch Postgres",
)
USER_A = UUID("00000000-0000-0000-0000-0000000014c1")
USER_B = UUID("00000000-0000-0000-0000-0000000014c2")
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hevy"


def _batch(*names: str) -> DetailedTrainingImportBatch:
    return HevyFixtureProvider(
        tuple((FIXTURES / name).read_bytes() for name in names)
    ).load_changes(None)


def _cleanup() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        for table in (
            "training_detail_sync_checkpoint",
            "training_detail_set",
            "training_detail_exercise",
            "training_detail_session_revision",
            "training_detail_session_tombstone",
        ):
            cur.execute(f"DELETE FROM {table} WHERE user_id IN (%s,%s)", (str(USER_A), str(USER_B)))


def test_latest_full_read_is_bounded_hydrated_owner_scoped_and_latest_revision_only() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    original = _batch("events_page_1.json", "events_page_2.json")
    repo.apply_import(USER_A, original, NOW)
    repo.apply_import(USER_A, _batch("events_updated_revision.json"), NOW)

    page = repo.list_latest_full(
        USER_A,
        1,
        source_system=DetailedTrainingSourceSystem.HEVY,
    )
    bench = repo.list_latest_full(
        USER_A,
        50,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-bench-template",
    )

    assert page.has_more is True and len(page.sessions) == 1
    assert page.sessions[0].session.title == "Push Day — corrected"
    assert len(bench.sessions) == 1
    assert bench.sessions[0].session.source_revision == (
        _batch("events_updated_revision.json").sessions[0].source_revision
    )
    assert bench.sessions[0].session.exercises[0].sets[0].reps == 9
    assert repo.list_latest_full(USER_B, 50).sessions == ()


def test_tombstone_removes_session_from_analytics_without_deleting_audit_revisions() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    original = _batch("events_page_1.json", "events_page_2.json")
    push = original.sessions[0]
    updated = _batch("events_updated_revision.json").sessions[0]
    repo.apply_import(
        USER_A,
        DetailedTrainingImportBatch((push, updated), (), True),
        NOW,
    )
    deletion = replace(original.deletions[0], source_session_id=push.source_session_id)
    repo.apply_import(USER_A, DetailedTrainingImportBatch((), (deletion,), True), NOW)

    assert repo.list_latest_full(USER_A, 50).sessions == ()
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            """
            SELECT COUNT(*) FROM training_detail_session_revision
            WHERE user_id=%s AND source_system='hevy' AND source_session_id=%s
            """,
            (str(USER_A), push.source_session_id),
        )
        assert cur.fetchone()[0] == 2
