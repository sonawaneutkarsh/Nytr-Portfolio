"""M5 health migration + persistence + RLS tests against a scratch Postgres.

Runs only when STACKS_TEST_DATABASE_URL is configured. These tests PROVE
(rather than assume) the raw-psycopg Supabase/RLS path required by the M5
review: a verified subject A can read/write only A's rows, and subject B can
neither read nor write A's rows, with every statement executed as
`SET LOCAL ROLE authenticated` + `request.jwt.claim.sub`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.db.sql_repos import SqlHealthBodyMassRepository
from nutrition_agent.domain.health.entities import (
    BodyMassSample,
    SampleDeletion,
    SyncBatch,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; requires scratch Postgres",
)

SUBJECT_A = "00000000-0000-0000-0000-00000000 0a41".replace(" ", "")
SUBJECT_A_UUID = UUID(SUBJECT_A)
SUBJECT_B = "00000000-0000-0000-0000-00000000 0b42".replace(" ", "")


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _connect_as(sub: str | None):
    """Raw psycopg connection running as `authenticated` with claim sub set."""
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    if sub is not None:
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (sub,))
    return conn, cur


def _cleanup() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "DELETE FROM health_body_mass_sample WHERE user_id IN (%s, %s)",
            (str(SUBJECT_A_UUID), SUBJECT_B),
        )
        conn.commit()


def _sample(uuid_int: int = 1, value: str = "72.000") -> BodyMassSample:
    return BodyMassSample(
        sample_uuid=UUID(int=uuid_int),
        value_kg=Decimal(value),
        sample_start=datetime(2026, 8, 21, 7, tzinfo=UTC),
        sample_end=datetime(2026, 8, 21, 7, tzinfo=UTC),
        source_name=None,
        source_bundle_id=None,
    )


def _batch(added=(), deleted=()) -> SyncBatch:
    return SyncBatch(client_batch_id=uuid4(), added=tuple(added), deletions=tuple(deleted))


# ---------------------------------------------------------------------------
# Migration + constraints
# ---------------------------------------------------------------------------


def test_health_migration_applies_cleanly_and_idempotently() -> None:
    _apply_migrations()
    _apply_migrations()  # re-run must succeed

    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = {row[0] for row in cur.fetchall()}
        assert "health_body_mass_sample" in tables
        cur.execute(
            "SELECT policyname FROM pg_policies WHERE tablename=%s",
            ("health_body_mass_sample",),
        )
        policies = {row[0] for row in cur.fetchall()}
        assert "hbm_owner_all" in policies
        cur.execute(
            "SELECT COUNT(*) FROM pg_trigger"
            " WHERE tgrelid='health_body_mass_sample'::regclass"
            " AND tgname='trg_health_body_mass_tombstone_only_update'"
            " AND NOT tgisinternal"
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT "
            "has_table_privilege('authenticated','health_body_mass_sample','SELECT'), "
            "has_table_privilege('authenticated','health_body_mass_sample','INSERT'), "
            "has_table_privilege('authenticated','health_body_mass_sample','UPDATE'), "
            "has_table_privilege('authenticated','health_body_mass_sample','DELETE'), "
            "has_column_privilege("
            "'authenticated','health_body_mass_sample','tombstoned_at','UPDATE'), "
            "has_column_privilege("
            "'authenticated','health_body_mass_sample','value_kg','UPDATE')"
        )
        assert cur.fetchone() == (True, True, False, False, True, False)


def test_check_constraints_enforce_tombstone_shape() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()

    def _insert(sql: str, params: tuple) -> bool:
        assert DATABASE_URL is not None
        conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
        try:
            cur = conn.cursor()
            cur.execute(sql, (str(uuid4()), *params))
            conn.commit()
            return True
        except Exception as exc:  # noqa: BLE001 - constraint violation expected
            conn.rollback()
            assert "chk_hbm" in str(exc), f"unexpected error: {exc}"
            return False
        finally:
            conn.close()

    owner = str(SUBJECT_A_UUID)
    # Active row without value must be rejected.
    assert not _insert(
        "INSERT INTO health_body_mass_sample (id, user_id, hk_sample_uuid)"
        " VALUES (%s, %s, '00000000-0000-0000-0000-00000000c001')",
        (owner,),
    )
    # Tombstone-only row WITH a value must be rejected.
    assert not _insert(
        "INSERT INTO health_body_mass_sample (id, user_id, hk_sample_uuid,"
        " value_kg, tombstoned_at) VALUES (%s, %s,"
        " '00000000-0000-0000-0000-00000000c002', 70, now())",
        (owner,),
    )
    # Value without sample_start must be rejected (joint shape).
    assert not _insert(
        "INSERT INTO health_body_mass_sample (id, user_id, hk_sample_uuid,"
        " value_kg) VALUES (%s, %s,"
        " '00000000-0000-0000-0000-00000000c003', 70)",
        (owner,),
    )
    # Valid active and valid tombstone-only rows are accepted.
    assert _insert(
        "INSERT INTO health_body_mass_sample (id, user_id, hk_sample_uuid,"
        " value_kg, sample_start, sample_end) VALUES (%s, %s,"
        " '00000000-0000-0000-0000-00000000c004', 72.000,"
        " '2026-08-21T07:00:00Z', '2026-08-21T07:00:00Z')",
        (owner,),
    )
    assert _insert(
        "INSERT INTO health_body_mass_sample (id, user_id, hk_sample_uuid,"
        " tombstoned_at) VALUES (%s, %s,"
        " '00000000-0000-0000-0000-00000000c005', now())",
        (owner,),
    )


# ---------------------------------------------------------------------------
# SQL repository semantics
# ---------------------------------------------------------------------------


def test_sql_repo_batch_is_idempotent_with_tombstones() -> None:
    pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    repo = SqlHealthBodyMassRepository(DATABASE_URL)

    first = repo.apply_batch(SUBJECT_A_UUID, _batch(added=[_sample()]))
    second = repo.apply_batch(SUBJECT_A_UUID, _batch(added=[_sample()]))
    assert (first.accepted_added, second.duplicate_added) == (1, 1)
    assert repo.status_summary(SUBJECT_A_UUID).record_count == 1

    deletion = repo.apply_batch(SUBJECT_A_UUID, _batch(deleted=[SampleDeletion(UUID(int=1))]))
    late_add = repo.apply_batch(SUBJECT_A_UUID, _batch(added=[_sample()]))
    assert deletion.applied_deletions == 1
    assert late_add.accepted_added == 0 and late_add.duplicate_added == 1

    status = repo.status_summary(SUBJECT_A_UUID)
    assert status.record_count == 1  # measurement retained under tombstone
    assert status.tombstone_count == 1
    assert status.latest_sample is None  # no ACTIVE samples remain


def test_sql_repo_deletion_before_add_creates_tombstone_only_row() -> None:
    pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    repo = SqlHealthBodyMassRepository(DATABASE_URL)

    outcome = repo.apply_batch(SUBJECT_A_UUID, _batch(deleted=[SampleDeletion(UUID(int=42))]))
    assert outcome.applied_deletions == 1
    again = repo.apply_batch(SUBJECT_A_UUID, _batch(deleted=[SampleDeletion(UUID(int=42))]))
    assert again.duplicate_deletions == 1
    late = repo.apply_batch(SUBJECT_A_UUID, _batch(added=[_sample(uuid_int=42)]))
    assert late.accepted_added == 0 and late.duplicate_added == 1

    psycopg0 = __import__("psycopg")
    assert DATABASE_URL is not None
    with psycopg0.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT value_kg, sample_start, sample_end, tombstoned_at IS NOT NULL"
            " FROM health_body_mass_sample"
            " WHERE user_id=%s AND hk_sample_uuid=%s",
            (str(SUBJECT_A_UUID), str(UUID(int=42))),
        )
        value_kg, start, end, tombstoned = cur.fetchone()
        assert value_kg is None and start is None and end is None
        assert tombstoned is True


def test_sql_repo_status_summary_aggregates() -> None:
    pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    repo = SqlHealthBodyMassRepository(DATABASE_URL)
    older = BodyMassSample(
        sample_uuid=UUID(int=1),
        value_kg=Decimal("66"),
        sample_start=datetime(2026, 8, 20, tzinfo=UTC),
        sample_end=datetime(2026, 8, 20, tzinfo=UTC),
        source_name=None,
        source_bundle_id=None,
    )
    repo.apply_batch(SUBJECT_A_UUID, _batch(added=[older, _sample(uuid_int=2)]))
    status = repo.status_summary(SUBJECT_A_UUID)
    assert status.record_count == 2
    assert status.tombstone_count == 0
    assert status.latest_sample is not None
    assert status.latest_sample.sample_uuid == UUID(int=2)
    assert str(status.latest_sample.value_kg) == "72.000"
    assert status.last_ingested_at is not None


# ---------------------------------------------------------------------------
# Mandatory RLS proof: verified subject A vs B through the raw psycopg path
# ---------------------------------------------------------------------------


def test_rls_owner_isolation_between_subject_a_and_b() -> None:
    """THE review-mandated proof. A sees/tombstones A's rows; B cannot touch them."""
    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    repo = SqlHealthBodyMassRepository(DATABASE_URL)
    repo.apply_batch(SUBJECT_A_UUID, _batch(added=[_sample()]))

    # Subject A: own rows are visible and the sole permitted update is usable.
    conn_a, cur_a = _connect_as(SUBJECT_A)
    try:
        cur_a.execute("SELECT COUNT(*) FROM health_body_mass_sample")
        assert cur_a.fetchone()[0] == 1
        cur_a.execute(
            "SELECT user_id FROM health_body_mass_sample WHERE hk_sample_uuid=%s",
            (str(UUID(int=1)),),
        )
        assert str(cur_a.fetchone()[0]) == SUBJECT_A
        cur_a.execute("UPDATE health_body_mass_sample SET tombstoned_at=now() WHERE TRUE")
        assert cur_a.rowcount >= 1
        conn_a.commit()
    except Exception:
        conn_a.rollback()
        raise
    finally:
        conn_a.close()

    # Subject B: A's rows are invisible; inserts claiming A's identity fail.
    conn_b, cur_b = _connect_as(SUBJECT_B)
    try:
        cur_b.execute("SELECT COUNT(*) FROM health_body_mass_sample")
        assert cur_b.fetchone()[0] == 0
        cur_b.execute(
            "SELECT hk_sample_uuid FROM health_body_mass_sample WHERE hk_sample_uuid=%s",
            (str(UUID(int=1)),),
        )
        assert cur_b.fetchone() is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_b.execute(
                "INSERT INTO health_body_mass_sample"
                " (id, user_id, hk_sample_uuid, value_kg,"
                " sample_start, sample_end)"
                " VALUES (%s, %s, '00000000-0000-0000-0000-00000000d001',"
                " 70, now(), now())",
                (str(uuid4()), str(SUBJECT_A_UUID)),
            )
        conn_b.commit()
    except Exception:
        conn_b.rollback()
        raise
    finally:
        conn_b.close()


@pytest.mark.parametrize(
    ("assignment", "replacement"),
    (
        ("id=%s", str(UUID(int=901))),
        ("user_id=%s", SUBJECT_B),
        ("hk_sample_uuid=%s", str(UUID(int=902))),
        ("metric=%s", "rewritten_metric"),
        ("value_kg=%s", "69.000"),
        ("sample_start=%s", datetime(2026, 8, 21, 6, tzinfo=UTC)),
        ("sample_end=%s", datetime(2026, 8, 21, 8, tzinfo=UTC)),
        ("source_name=%s", "rewritten source"),
        ("source_bundle_id=%s", "rewritten.bundle"),
        ("ingested_at=%s", datetime(2026, 8, 22, 7, tzinfo=UTC)),
    ),
)
def test_trusted_writer_cannot_rewrite_body_mass_facts(
    assignment: str,
    replacement: object,
) -> None:
    """The trigger protects every factual column even for a trusted DB writer."""

    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    SqlHealthBodyMassRepository(DATABASE_URL).apply_batch(
        SUBJECT_A_UUID,
        _batch(added=[_sample()]),
    )
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute(
                f"UPDATE health_body_mass_sample SET {assignment} "  # noqa: S608
                "WHERE user_id=%s AND hk_sample_uuid=%s",
                (replacement, str(SUBJECT_A_UUID), str(UUID(int=1))),
            )
        conn.rollback()


def test_first_tombstone_is_the_only_legal_body_mass_update() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    SqlHealthBodyMassRepository(DATABASE_URL).apply_batch(
        SUBJECT_A_UUID,
        _batch(added=[_sample()]),
    )
    assert DATABASE_URL is not None
    first_tombstone = datetime(2026, 8, 22, 7, tzinfo=UTC)
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "UPDATE health_body_mass_sample SET tombstoned_at=%s"
            " WHERE user_id=%s AND hk_sample_uuid=%s",
            (first_tombstone, str(SUBJECT_A_UUID), str(UUID(int=1))),
        )
        assert cur.rowcount == 1

    for replacement in (
        first_tombstone,
        first_tombstone + timedelta(seconds=1),
        None,
    ):
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
                cur.execute(
                    "UPDATE health_body_mass_sample SET tombstoned_at=%s"
                    " WHERE user_id=%s AND hk_sample_uuid=%s",
                    (replacement, str(SUBJECT_A_UUID), str(UUID(int=1))),
                )
            conn.rollback()

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT tombstoned_at FROM health_body_mass_sample"
            " WHERE user_id=%s AND hk_sample_uuid=%s",
            (str(SUBJECT_A_UUID), str(UUID(int=1))),
        )
        assert cur.fetchone()[0] == first_tombstone


def test_authenticated_owner_cannot_rewrite_or_delete_body_mass_facts() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    SqlHealthBodyMassRepository(DATABASE_URL).apply_batch(
        SUBJECT_A_UUID,
        _batch(added=[_sample()]),
    )

    conn_a, cur_a = _connect_as(SUBJECT_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("UPDATE health_body_mass_sample SET value_kg='69.000'")
        conn_a.rollback()
    finally:
        conn_a.close()

    conn_a, cur_a = _connect_as(SUBJECT_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur_a.execute("DELETE FROM health_body_mass_sample")
        conn_a.rollback()
    finally:
        conn_a.close()


def test_rls_hides_cross_owner_tombstone_update() -> None:
    _apply_migrations()
    _cleanup()
    SqlHealthBodyMassRepository(DATABASE_URL).apply_batch(
        SUBJECT_A_UUID,
        _batch(added=[_sample()]),
    )

    conn_b, cur_b = _connect_as(SUBJECT_B)
    try:
        cur_b.execute(
            "UPDATE health_body_mass_sample SET tombstoned_at=now()"
            " WHERE user_id=%s AND hk_sample_uuid=%s",
            (str(SUBJECT_A_UUID), str(UUID(int=1))),
        )
        assert cur_b.rowcount == 0
        conn_b.commit()
    finally:
        conn_b.close()

    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "SELECT tombstoned_at FROM health_body_mass_sample"
            " WHERE user_id=%s AND hk_sample_uuid=%s",
            (str(SUBJECT_A_UUID), str(UUID(int=1))),
        )
        assert cur.fetchone()[0] is None


def test_rls_blocks_access_without_jwt_claim() -> None:
    """No request.jwt.claim.sub => no row access even as `authenticated`."""
    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _cleanup()
    repo = SqlHealthBodyMassRepository(DATABASE_URL)
    repo.apply_batch(SUBJECT_A_UUID, _batch(added=[_sample()]))

    conn, cur = _connect_as(None)
    try:
        cur.execute("SELECT COUNT(*) FROM health_body_mass_sample")
        assert cur.fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute(
                "INSERT INTO health_body_mass_sample"
                " (id, user_id, hk_sample_uuid, value_kg, sample_start,"
                " sample_end) VALUES (%s, %s,"
                " '00000000-0000-0000-0000-00000000d002', 70, now(), now())",
                (str(uuid4()), str(SUBJECT_A_UUID)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
