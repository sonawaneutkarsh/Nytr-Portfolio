"""PostgreSQL 16 proofs for atomic Hevy imports and immutable sync checkpoints."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.db.sql_repos import SqlDetailedTrainingRepository
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingSourceSystem,
    DetailedTrainingSyncCheckpoint,
    DetailedTrainingSyncMode,
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


def _apply() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _batch(*names: str) -> DetailedTrainingImportBatch:
    return HevyFixtureProvider(
        tuple((FIXTURES / name).read_bytes() for name in names)
    ).load_changes(None)


def _checkpoint(
    checkpoint_id: UUID,
    user_id: UUID,
    *,
    mode: DetailedTrainingSyncMode = DetailedTrainingSyncMode.BOOTSTRAP,
    watermark: datetime = NOW,
    completed_at: datetime = NOW,
) -> DetailedTrainingSyncCheckpoint:
    return DetailedTrainingSyncCheckpoint(
        checkpoint_id=checkpoint_id,
        user_id=user_id,
        source_system=DetailedTrainingSourceSystem.HEVY,
        sync_mode=mode,
        bootstrap_completed=True,
        source_event_watermark=watermark,
        parser_version="hevy-public-api.v1",
        provider_version="hevy-api-provider.v1",
        pages_fetched=1,
        logical_requests=1,
        attempts_made=1,
        retries=0,
        completed_at=completed_at,
    )


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
            cur.execute(
                f"DELETE FROM {table} WHERE user_id IN (%s,%s)",
                (str(USER_A), str(USER_B)),
            )


def _connect_as(user_id: UUID):
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
    return conn, cur


def test_migration_0012_reapplies_with_append_only_owner_rls() -> None:
    _apply()
    _apply()
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute("SELECT to_regclass('public.training_detail_sync_checkpoint')")
        assert cur.fetchone()[0] == "training_detail_sync_checkpoint"
        cur.execute(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE oid='training_detail_sync_checkpoint'::regclass"
        )
        assert cur.fetchone()[0] is True
        cur.execute(
            "SELECT has_table_privilege('authenticated',"
            "'training_detail_sync_checkpoint','SELECT'), "
            "has_table_privilege('authenticated',"
            "'training_detail_sync_checkpoint','INSERT'), "
            "has_table_privilege('authenticated',"
            "'training_detail_sync_checkpoint','UPDATE'), "
            "has_table_privilege('authenticated',"
            "'training_detail_sync_checkpoint','DELETE')"
        )
        assert cur.fetchone() == (True, True, False, False)
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='training_detail_sync_checkpoint'"
        )
        assert "api_key" not in {str(row[0]) for row in cur.fetchall()}


def test_sql_sync_persists_detail_and_checkpoint_and_replay_is_idempotent() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    batch = _batch("events_updated_revision.json")
    first = repo.apply_sync(USER_A, batch, NOW, _checkpoint(UUID(int=1), USER_A))
    replay = repo.apply_sync(
        USER_A,
        batch,
        NOW + timedelta(minutes=1),
        _checkpoint(
            UUID(int=2),
            USER_A,
            mode=DetailedTrainingSyncMode.INCREMENTAL,
            completed_at=NOW + timedelta(minutes=1),
        ),
    )

    assert (first.sessions_created, replay.duplicate_revisions) == (1, 1)
    assert repo.latest_sync_checkpoint(USER_A, DetailedTrainingSourceSystem.HEVY) == _checkpoint(
        UUID(int=2),
        USER_A,
        mode=DetailedTrainingSyncMode.INCREMENTAL,
        completed_at=NOW + timedelta(minutes=1),
    )
    assert repo.latest_sync_checkpoint(USER_B, DetailedTrainingSourceSystem.HEVY) is None


def test_checkpoint_insert_failure_rolls_back_earlier_revision_writes() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    original = _batch("events_updated_revision.json")
    repo.apply_sync(USER_A, original, NOW, _checkpoint(UUID(int=10), USER_A))
    new_session = replace(
        original.sessions[0],
        source_session_id="must-roll-back",
        source_revision="2026-09-04T13:00:00+00:00",
        source_updated_at=NOW + timedelta(hours=1),
        source_payload_sha256="f" * 64,
    )

    with pytest.raises(Exception) as error:
        repo.apply_sync(
            USER_A,
            DetailedTrainingImportBatch((new_session,), (), True),
            NOW + timedelta(hours=1),
            _checkpoint(
                UUID(int=10),
                USER_A,
                mode=DetailedTrainingSyncMode.INCREMENTAL,
                watermark=NOW + timedelta(hours=1),
                completed_at=NOW + timedelta(hours=1),
            ),
        )
    assert error.type.__name__ == "UniqueViolation"
    assert {item.source_session_id for item in repo.list_latest(USER_A, 50)} == {
        original.sessions[0].source_session_id
    }
    assert repo.latest_sync_checkpoint(
        USER_A, DetailedTrainingSourceSystem.HEVY
    ).checkpoint_id == UUID(int=10)  # type: ignore[union-attr]


def test_checkpoint_order_and_watermark_cannot_regress() -> None:
    _apply()
    _cleanup()
    repo = SqlDetailedTrainingRepository(DATABASE_URL)
    empty = DetailedTrainingImportBatch((), (), True)
    repo.apply_sync(USER_A, empty, NOW, _checkpoint(UUID(int=20), USER_A))

    with pytest.raises(Exception) as mode_error:
        repo.apply_sync(
            USER_A,
            empty,
            NOW,
            _checkpoint(UUID(int=21), USER_A, mode=DetailedTrainingSyncMode.BOOTSTRAP),
        )
    assert mode_error.type.__name__ == "CheckViolation"

    with pytest.raises(Exception) as watermark_error:
        repo.apply_sync(
            USER_A,
            empty,
            NOW,
            _checkpoint(
                UUID(int=22),
                USER_A,
                mode=DetailedTrainingSyncMode.INCREMENTAL,
                watermark=NOW - timedelta(seconds=1),
            ),
        )
    assert watermark_error.type.__name__ == "CheckViolation"
    assert repo.latest_sync_checkpoint(
        USER_A, DetailedTrainingSourceSystem.HEVY
    ).checkpoint_id == UUID(int=20)  # type: ignore[union-attr]


def test_checkpoint_rls_blocks_cross_owner_insert_read_update_and_delete() -> None:
    _apply()
    _cleanup()
    SqlDetailedTrainingRepository(DATABASE_URL).apply_sync(
        USER_A,
        DetailedTrainingImportBatch((), (), True),
        NOW,
        _checkpoint(UUID(int=30), USER_A),
    )

    conn_b, cur_b = _connect_as(USER_B)
    try:
        cur_b.execute("SELECT COUNT(*) FROM training_detail_sync_checkpoint")
        assert cur_b.fetchone()[0] == 0
        with pytest.raises(Exception) as insert_error:
            cur_b.execute(
                """
                INSERT INTO training_detail_sync_checkpoint (
                    checkpoint_id,user_id,source_system,sync_mode,
                    bootstrap_completed,source_event_watermark,parser_version,
                    provider_version,pages_fetched,logical_requests,
                    attempts_made,retries,completed_at)
                VALUES (%s,%s,'hevy','bootstrap',true,%s,'parser','provider',1,1,1,0,%s)
                """,
                (str(UUID(int=31)), str(USER_A), NOW, NOW),
            )
        assert insert_error.type.__name__ == "InsufficientPrivilege"
        conn_b.rollback()
    finally:
        conn_b.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(Exception) as update_error:
            cur_a.execute(
                "UPDATE training_detail_sync_checkpoint SET retries=0 WHERE checkpoint_id=%s",
                (str(UUID(int=30)),),
            )
        assert update_error.type.__name__ == "InsufficientPrivilege"
        conn_a.rollback()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(Exception) as delete_error:
            cur_a.execute(
                "DELETE FROM training_detail_sync_checkpoint WHERE checkpoint_id=%s",
                (str(UUID(int=30)),),
            )
        assert delete_error.type.__name__ == "InsufficientPrivilege"
        conn_a.rollback()
    finally:
        conn_a.close()
