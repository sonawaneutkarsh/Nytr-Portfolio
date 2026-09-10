"""PostgreSQL 16 proofs for M14A detail revisions, RLS, and tombstones."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.application.ports import DetailedTrainingRevisionConflictError
from nutrition_agent.db.sql_repos import SqlDetailedTrainingRepository
from nutrition_agent.domain.training.detail import DetailedTrainingImportBatch
from nutrition_agent.infrastructure.hevy_detail_source import HevyFixtureProvider
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; requires scratch Postgres",
)
USER_A = UUID("00000000-0000-0000-0000-0000000014a1")
USER_B = UUID("00000000-0000-0000-0000-0000000014b2")
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hevy"


def _apply() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


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


def _connect_as(user_id: UUID):
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
    return conn, cur


def test_training_detail_migrations_reapply_with_normalized_append_only_owner_rls() -> None:
    _apply()
    _apply()
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        expected = {
            "training_detail_session_revision",
            "training_detail_exercise",
            "training_detail_set",
            "training_detail_session_tombstone",
            "training_detail_sync_checkpoint",
        }
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'training_detail_%'"
        )
        assert {row[0] for row in cur.fetchall()} == expected
        for table in expected:
            cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (table,))
            assert cur.fetchone()[0] is True
            cur.execute(
                "SELECT has_table_privilege('authenticated',%s,'SELECT'), "
                "has_table_privilege('authenticated',%s,'INSERT'), "
                "has_table_privilege('authenticated',%s,'UPDATE'), "
                "has_table_privilege('authenticated',%s,'DELETE')",
                (table, table, table, table),
            )
            assert cur.fetchone() == (True, True, False, False)


def test_sql_repository_round_trip_is_idempotent_ordered_and_decimal_exact() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    batch = _batch("events_page_1.json", "events_page_2.json")
    first = repo.apply_import(USER_A, batch, NOW)
    replay = repo.apply_import(USER_A, batch, NOW)
    summaries = repo.list_latest(USER_A, 50)
    assert (first.accepted_revisions, replay.duplicate_revisions) == (2, 2)
    assert tuple(item.title for item in summaries) == ("Push Day", "Lower Day")
    assert (summaries[0].exercise_count, summaries[0].set_count) == (6, 9)
    stored = repo.get_by_revision_id(USER_A, summaries[0].revision_id)
    assert stored is not None
    load = stored.session.exercises[0].sets[1].load
    assert load is not None and str(load.value) == "65"
    assert stored.session.exercises[3].sets[0].load is None
    assert repo.list_latest(USER_B, 50) == ()
    assert repo.get_by_revision_id(USER_B, summaries[0].revision_id) is None


def test_source_update_appends_revision_and_conflict_rolls_back() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    original = _batch("events_page_1.json", "events_page_2.json")
    repo.apply_import(USER_A, original, NOW)
    updated = _batch("events_updated_revision.json")
    assert repo.apply_import(USER_A, updated, NOW).accepted_revisions == 1
    assert repo.list_latest(USER_A, 50)[0].title == "Push Day — corrected"
    conflict = replace(updated.sessions[0], title="conflict", source_payload_sha256="f" * 64)
    with pytest.raises(DetailedTrainingRevisionConflictError):
        repo.apply_import(USER_A, DetailedTrainingImportBatch((conflict,), (), True), NOW)
    assert repo.list_latest(USER_A, 50)[0].title == "Push Day — corrected"

    new_session = replace(
        original.sessions[1],
        source_session_id="fixture-new-session-before-conflict",
        source_payload_sha256="e" * 64,
    )
    with pytest.raises(DetailedTrainingRevisionConflictError):
        repo.apply_import(
            USER_A,
            DetailedTrainingImportBatch((new_session, conflict), (), True),
            NOW,
        )
    assert "fixture-new-session-before-conflict" not in {
        item.source_session_id for item in repo.list_latest(USER_A, 50)
    }


def test_tombstone_hides_history_and_prevents_later_source_resurrection() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    batch = _batch("events_page_1.json", "events_page_2.json")
    push = batch.sessions[0]
    repo.apply_import(USER_A, DetailedTrainingImportBatch((push,), (), True), NOW)
    deletion = replace(batch.deletions[0], source_session_id=push.source_session_id)
    assert (
        repo.apply_import(
            USER_A, DetailedTrainingImportBatch((), (deletion,), True), NOW
        ).applied_deletions
        == 1
    )
    assert (
        repo.apply_import(
            USER_A, DetailedTrainingImportBatch((push,), (), True), NOW
        ).blocked_by_tombstone
        == 1
    )
    assert repo.list_latest(USER_A, 50) == ()


def test_cross_owner_parent_injection_update_and_delete_are_denied() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    repo.apply_import(USER_A, _batch("events_updated_revision.json"), NOW)
    revision_id = repo.list_latest(USER_A, 50)[0].revision_id

    conn_b, cur_b = _connect_as(USER_B)
    try:
        cur_b.execute("SELECT COUNT(*) FROM training_detail_session_revision")
        assert cur_b.fetchone()[0] == 0
        with pytest.raises(Exception) as insert_error:
            cur_b.execute(
                """
                INSERT INTO training_detail_exercise (
                    exercise_id,user_id,revision_id,occurrence_identity,
                    source_exercise_id,display_name,exercise_order)
                    VALUES (%s,%s,%s,'attack','attack','attack',99)
                """,
                (str(UUID(int=99)), str(USER_B), str(revision_id)),
            )
        assert insert_error.type.__name__ in {"ForeignKeyViolation", "InsufficientPrivilege"}
        conn_b.rollback()
    finally:
        conn_b.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(Exception) as update_error:
            cur_a.execute("UPDATE training_detail_session_revision SET title='changed'")
        assert update_error.type.__name__ == "InsufficientPrivilege"
        conn_a.rollback()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(Exception) as delete_error:
            cur_a.execute("DELETE FROM training_detail_session_revision")
        assert delete_error.type.__name__ == "InsufficientPrivilege"
        conn_a.rollback()
    finally:
        conn_a.close()


def test_set_constraints_and_database_resurrection_guard_fail_closed() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    assert DATABASE_URL is not None
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    batch = _batch("events_updated_revision.json")
    repo.apply_import(USER_A, batch, NOW)
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT exercise_id FROM training_detail_exercise WHERE user_id=%s LIMIT 1",
            (str(USER_A),),
        )
        exercise_id = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):  # type: ignore[attr-defined]
            cur.execute(
                """
                INSERT INTO training_detail_set (
                    set_id,user_id,exercise_id,set_identity,set_index,set_type,
                    load_value,load_unit)
                VALUES (%s,%s,%s,'bad-type',99,'unsupported',10,'kg')
                """,
                (str(UUID(int=501)), str(USER_A), exercise_id),
            )
        conn.rollback()

    deletion = replace(
        _batch("events_page_1.json", "events_page_2.json").deletions[0],
        source_session_id=batch.sessions[0].source_session_id,
    )
    repo.apply_import(USER_A, DetailedTrainingImportBatch((), (deletion,), True), NOW)
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute(
                """
                INSERT INTO training_detail_session_revision (
                    revision_id,user_id,source_system,source_session_id,
                    source_revision,title,started_at,ended_at,parser_version,
                    source_payload_sha256,ingested_at)
                VALUES (%s,%s,'hevy',%s,'later','late',%s,%s,'parser',%s,%s)
                """,
                (
                    str(UUID(int=502)),
                    str(USER_A),
                    batch.sessions[0].source_session_id,
                    NOW,
                    NOW,
                    "a" * 64,
                    NOW,
                ),
            )
