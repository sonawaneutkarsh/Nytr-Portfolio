"""PostgreSQL 16 proof for M12A training persistence, RLS, and tombstones."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.db.sql_repos import SqlTrainingSessionRepository
from nutrition_agent.domain.training import (
    TrainingSessionDeletion,
    TrainingSessionObservation,
    TrainingSourceSystem,
    TrainingSyncBatch,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; requires scratch Postgres",
)

USER_A = UUID("00000000-0000-0000-0000-0000000010a1")
USER_B = UUID("00000000-0000-0000-0000-0000000010b2")
T0 = datetime(2026, 9, 3, 12, tzinfo=UTC)
SOURCE_ONE = UUID(int=1)


def _apply() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _cleanup() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "DELETE FROM training_session_tombstone WHERE user_id IN (%s,%s)",
            (str(USER_A), str(USER_B)),
        )
        cur.execute(
            "DELETE FROM training_session WHERE user_id IN (%s,%s)",
            (str(USER_A), str(USER_B)),
        )


def _connect_as(user_id: UUID | None):
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    if user_id is not None:
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
    return conn, cur


def _observation(source_id: UUID = SOURCE_ONE) -> TrainingSessionObservation:
    return TrainingSessionObservation(
        source_system=TrainingSourceSystem.HEALTHKIT,
        source_record_id=str(source_id),
        activity_type="37",
        started_at=T0 - timedelta(hours=1),
        ended_at=T0,
        active_duration_seconds=Decimal("3600.000"),
        active_energy_kcal=Decimal("412.750"),
        timezone_identifier="America/New_York",
        source_name="Apple Watch",
        source_bundle_id="com.apple.health",
        source_revision="26.0",
    )


def _batch(*, added=(), deletions=()) -> TrainingSyncBatch:
    return TrainingSyncBatch(uuid4(), tuple(added), tuple(deletions))


def _deletion(source_id: UUID = SOURCE_ONE) -> TrainingSessionDeletion:
    return TrainingSessionDeletion(TrainingSourceSystem.HEALTHKIT, str(source_id))


def test_migration_0010_applies_twice_with_expected_schema_rls_and_privileges() -> None:
    _apply()
    _apply()
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'training_session%'"
        )
        assert {row[0] for row in cur.fetchall()} == {
            "training_session",
            "training_session_tombstone",
        }
        cur.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='training_session'"
        )
        columns = dict(cur.fetchall())
        assert columns["started_at"] == "NO"
        assert columns["ended_at"] == "NO"
        assert columns["active_energy_kcal"] == "YES"
        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid='training_session'::regclass")
        assert cur.fetchone()[0] is True
        cur.execute(
            "SELECT has_table_privilege('authenticated','training_session','DELETE'), "
            "has_column_privilege('authenticated','training_session','started_at','UPDATE'), "
            "has_column_privilege('authenticated','training_session','tombstoned_at','UPDATE')"
        )
        assert cur.fetchone() == (False, False, True)


def test_schema_constraints_and_unique_source_identity() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    assert DATABASE_URL is not None

    def insert(*, source_id: str, start: str, end: str, duration: str, energy: str) -> None:
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
            cur.execute(
                """
                INSERT INTO training_session (
                    session_id,user_id,source_system,source_record_id,
                    started_at,ended_at,active_duration_seconds,active_energy_kcal)
                VALUES (%s,%s,'healthkit',%s,%s,%s,%s,%s)
                """,
                (str(uuid4()), str(USER_A), source_id, start, end, duration, energy),
            )

    with pytest.raises(psycopg.errors.CheckViolation):  # type: ignore[attr-defined]
        insert(
            source_id=str(UUID(int=1)),
            start="2026-09-03T12:00:00Z",
            end="2026-09-03T11:00:00Z",
            duration="1",
            energy="1",
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # type: ignore[attr-defined]
        insert(
            source_id=str(UUID(int=2)),
            start="2026-09-03T11:00:00Z",
            end="2026-09-03T12:00:00Z",
            duration="-1",
            energy="1",
        )
    with pytest.raises(psycopg.errors.CheckViolation):  # type: ignore[attr-defined]
        insert(
            source_id=str(UUID(int=3)),
            start="2026-09-03T11:00:00Z",
            end="2026-09-03T12:00:00Z",
            duration="1",
            energy="-1",
        )
    insert(
        source_id=str(UUID(int=4)),
        start="2026-09-03T11:00:00Z",
        end="2026-09-03T12:00:00Z",
        duration="3600.000",
        energy="412.750",
    )
    with pytest.raises(psycopg.errors.UniqueViolation):  # type: ignore[attr-defined]
        insert(
            source_id=str(UUID(int=4)),
            start="2026-09-03T11:00:00Z",
            end="2026-09-03T12:00:00Z",
            duration="3600",
            energy="400",
        )


def test_sql_repository_is_idempotent_ordered_and_preserves_provenance() -> None:
    pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    repo = SqlTrainingSessionRepository(DATABASE_URL)
    first = repo.apply_batch(USER_A, _batch(added=[_observation(UUID(int=2)), _observation()]))
    replay = repo.apply_batch(USER_A, _batch(added=[_observation()]))
    found = repo.list_active(USER_A, T0 - timedelta(days=1), T0 + timedelta(seconds=1))
    assert (first.accepted_added, replay.duplicate_added) == (2, 1)
    assert tuple(item.source_record_id for item in found) == (str(UUID(int=1)), str(UUID(int=2)))
    assert found[0].active_duration_seconds == Decimal("3600.000")
    assert found[0].active_energy_kcal == Decimal("412.750")
    assert found[0].source_name == "Apple Watch"
    assert found[0].source_bundle_id == "com.apple.health"
    assert found[0].source_revision == "26.0"


def test_tombstone_after_add_and_deletion_before_add_never_resurrect() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    repo = SqlTrainingSessionRepository(DATABASE_URL)
    repo.apply_batch(USER_A, _batch(added=[_observation()]))
    removed = repo.apply_batch(USER_A, _batch(deletions=[_deletion()]))
    repeat = repo.apply_batch(USER_A, _batch(deletions=[_deletion()]))
    late = repo.apply_batch(USER_A, _batch(added=[_observation()]))
    assert removed.applied_deletions == 1
    assert repeat.duplicate_deletions == 1
    assert late.duplicate_added == 1
    assert repo.list_active(USER_A, T0 - timedelta(days=1), T0 + timedelta(days=1)) == ()

    early = repo.apply_batch(USER_A, _batch(deletions=[_deletion(UUID(int=9))]))
    late_unknown = repo.apply_batch(USER_A, _batch(added=[_observation(UUID(int=9))]))
    assert early.applied_deletions == 1 and late_unknown.duplicate_added == 1

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute(
                """
                INSERT INTO training_session (
                    session_id,user_id,source_system,source_record_id,started_at,ended_at)
                VALUES (%s,%s,'healthkit',%s,%s,%s)
                """,
                (
                    str(uuid4()),
                    str(USER_A),
                    str(UUID(int=9)),
                    T0 - timedelta(hours=1),
                    T0,
                ),
            )
        conn_a.rollback()
    finally:
        conn_a.close()


def test_owner_rls_immutability_and_no_delete_are_enforced() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    SqlTrainingSessionRepository(DATABASE_URL).apply_batch(USER_A, _batch(added=[_observation()]))

    conn_a, cur_a = _connect_as(USER_A)
    try:
        cur_a.execute("SELECT COUNT(*) FROM training_session")
        assert cur_a.fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("UPDATE training_session SET started_at=started_at + interval '1 second'")
        conn_a.rollback()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        cur_a.execute("UPDATE training_session SET tombstoned_at=now()")
        assert cur_a.rowcount == 1
        conn_a.commit()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("UPDATE training_session SET tombstoned_at=now()")
        conn_a.rollback()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("DELETE FROM training_session")
        conn_a.rollback()
    finally:
        conn_a.close()

    # Materialize the matching immutable identity ledger so the cross-owner
    # read below proves RLS on both M12A tables, not merely an empty-table case.
    SqlTrainingSessionRepository(DATABASE_URL).apply_batch(USER_A, _batch(deletions=[_deletion()]))

    conn_b, cur_b = _connect_as(USER_B)
    try:
        cur_b.execute("SELECT COUNT(*) FROM training_session")
        assert cur_b.fetchone()[0] == 0
        cur_b.execute("SELECT COUNT(*) FROM training_session_tombstone")
        assert cur_b.fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_b.execute(
                """
                INSERT INTO training_session (
                    session_id,user_id,source_system,source_record_id,started_at,ended_at)
                VALUES (%s,%s,'healthkit',%s,now(),now())
                """,
                (str(uuid4()), str(USER_A), str(UUID(int=88))),
            )
        conn_b.rollback()
    finally:
        conn_b.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("UPDATE training_session_tombstone SET tombstoned_at=now()")
        conn_a.rollback()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(USER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("DELETE FROM training_session_tombstone")
        conn_a.rollback()
    finally:
        conn_a.close()


def test_observation_trigger_blocks_trusted_writer_rewrites() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            """
            INSERT INTO training_session (
                session_id,user_id,source_system,source_record_id,
                started_at,ended_at,active_energy_kcal)
            VALUES (%s,%s,'healthkit',%s,%s,%s,'100.000')
            """,
            (
                str(uuid4()),
                str(USER_A),
                str(UUID(int=77)),
                T0 - timedelta(hours=1),
                T0,
            ),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute(
                "UPDATE training_session SET active_energy_kcal='101.000' "
                "WHERE user_id=%s AND source_record_id=%s",
                (str(USER_A), str(UUID(int=77))),
            )
        conn.rollback()


def test_missing_jwt_claim_sees_no_rows_and_cannot_insert_tombstone() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply()
    _cleanup()
    SqlTrainingSessionRepository(DATABASE_URL).apply_batch(USER_A, _batch(added=[_observation()]))
    conn, cur = _connect_as(None)
    try:
        cur.execute("SELECT COUNT(*) FROM training_session")
        assert cur.fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute(
                """
                INSERT INTO training_session_tombstone (
                    tombstone_id,user_id,source_system,source_record_id)
                VALUES (%s,%s,'healthkit',%s)
                """,
                (str(uuid4()), str(USER_A), str(UUID(int=1))),
            )
        conn.rollback()
    finally:
        conn.close()
