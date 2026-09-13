"""Migration 0018 append-only privilege and owner-isolation proofs."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0018_body_goals.sql"
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="scratch PostgreSQL required")


def _owner_connection(owner: object):
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    connection = psycopg.connect(DATABASE_URL)
    cursor = connection.cursor()
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (str(owner),))
    return connection, cursor


def test_migration_reapplies_with_rls_and_append_only_grants() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        for table in (
            "body_goal_profile_version",
            "waist_measurement",
            "starting_calorie_proposal",
            "starting_calorie_proposal_decision",
        ):
            cursor.execute(
                """SELECT has_table_privilege('authenticated', %s, 'SELECT'),
                has_table_privilege('authenticated', %s, 'INSERT'),
                has_table_privilege('authenticated', %s, 'UPDATE'),
                has_table_privilege('authenticated', %s, 'DELETE'), relrowsecurity
                FROM pg_class WHERE oid=%s::regclass""",
                (table, table, table, table, table),
            )
            assert cursor.fetchone() == (True, True, False, False, True)


def test_profile_and_waist_rows_are_owner_scoped_and_immutable() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    owner, other = uuid4(), uuid4()
    profile_id, measurement_id = uuid4(), uuid4()
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)

    connection, cursor = _owner_connection(owner)
    try:
        cursor.execute(
            """INSERT INTO body_goal_profile_version
            (profile_id,user_id,policy_version,height_cm,date_of_birth,formula_sex,
             activity_level,target_weight_kg,provenance,payload_sha256,created_at)
            VALUES (%s,%s,'owner-body-goal-profile.v1',175,%s,'male','lightly_active',
                    NULL,'owner_entered',%s,%s)""",
            (profile_id, owner, date(1995, 1, 1), "a" * 64, now),
        )
        cursor.execute(
            """INSERT INTO waist_measurement
            (measurement_id,user_id,measured_at,value_cm,entered_value,entered_unit,
             provenance,corrects_measurement_id,recorded_at)
            VALUES (%s,%s,%s,81.28,32,'in','owner_entered',NULL,%s)""",
            (measurement_id, owner, now, now),
        )
        connection.commit()
    finally:
        connection.close()

    connection, cursor = _owner_connection(other)
    try:
        cursor.execute("SELECT count(*) FROM body_goal_profile_version")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM waist_measurement")
        assert cursor.fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute("UPDATE waist_measurement SET value_cm=99")
        connection.rollback()
        cursor.execute("SET ROLE authenticated")
        cursor.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (str(owner),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute("DELETE FROM body_goal_profile_version")
        connection.rollback()
    finally:
        connection.close()
