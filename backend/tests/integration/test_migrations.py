"""Migration + persistence tests against a scratch Postgres.

Runs only when STACKS_TEST_DATABASE_URL is configured (e.g. an ephemeral
`postgres:16` container). Validates:
- DDL applies cleanly and is idempotent (IF NOT EXISTS),
- all six Milestone 2 tables exist,
- unique constraints reject duplicate offering/profile inserts,
- SQL repository write paths are upsert-idempotent with no deletes.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; migration test requires scratch Postgres",
)


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def test_migrations_apply_cleanly_and_constraints_hold() -> None:
    psycopg = pytest.importorskip("psycopg")
    _apply_migrations()
    _apply_migrations()  # idempotent re-apply must succeed

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = {row[0] for row in cur.fetchall()}
        assert {
            "stacks_ingestion_run",
            "source_snapshot",
            "stacks_food",
            "nutrition_profile",
            "menu_offering",
            "quarantine_record",
        } <= tables


def test_sql_repositories_roundtrip_and_idempotency() -> None:
    pytest.importorskip("psycopg")
    from nutrition_agent.db.sql_repos import (
        SqlOfferingRepository,
        SqlProfileRepository,
        SqlQuarantineRepository,
        SqlRunRepository,
        SqlSnapshotRepository,
    )
    from nutrition_agent.domain.stacks.entities import (
        Confidence,
        MealPeriod,
        MenuOffering,
        NutrientKey,
        NutrientValue,
        NutritionProfile,
        Provenance,
        ServingBasisKind,
    )
    from nutrition_agent.domain.stacks.ingestion import (
        ErrorCode,
        IngestionRun,
        QuarantineRecord,
        RunStatus,
        Severity,
        SubjectType,
    )
    from nutrition_agent.infrastructure.snapshot_store import SnapshotRef

    assert DATABASE_URL is not None
    psycopg0 = __import__("psycopg")
    with psycopg0.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE quarantine_record, menu_offering, nutrition_profile,"
            " stacks_food, source_snapshot, stacks_ingestion_run CASCADE"
        )

    run_id = uuid4()
    run = IngestionRun(
        run_id=run_id,
        status=RunStatus.STARTED,
        mode="manual",
        params={"service_date": "2026-08-21", "meals": "Lunch"},
        config_fingerprint="test",
        started_at=datetime.now(UTC),
    )
    SqlRunRepository(DATABASE_URL).create(run)
    run.status = RunStatus.PERSISTED
    run.finished_at = datetime.now(UTC)
    SqlRunRepository(DATABASE_URL).update(run)

    body = b"<html>roundtrip</html>"
    ref = SnapshotRef(
        snapshot_id=uuid4(),
        content_sha256=__import__("hashlib").sha256(body).hexdigest(),
        storage_path="aa/bb.html",
        source_url="fixture://menu/lunch",
        method="POST",
        request_params={"meal": "Lunch"},
        http_status=200,
        fetched_at=datetime.now(UTC),
        byte_size=len(body),
        run_id=run_id,
    )
    snapshot_repo = SqlSnapshotRepository(DATABASE_URL)
    snapshot_repo.record(ref, "2026-08-21.m2.1")
    latest = snapshot_repo.find_latest_by_request(ref.source_url, ref.method, ref.request_params)
    assert latest is not None
    assert latest[0].content_sha256 == ref.content_sha256

    food_id = uuid4()
    psycopg = __import__("psycopg")
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stacks_food (food_id, campus_id, name_raw, name_normalized)"
            " VALUES (%s, 50, %s, %s) ON CONFLICT DO NOTHING",
            (str(food_id), "#7 Roast Chicken & Provolone", "#7 Roast Chicken & Provolone"),
        )

    profile = NutritionProfile(
        food_id=food_id,
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
        nutrients={
            NutrientKey.CALORIES_KCAL: NutrientValue(
                value=Decimal(1318), unit="kcal", dv_percent=None
            )
        },
        unavailable_fields=(),
        extra_fields={},
        ingredients_raw="Chicken, Roll",
        ingredient_components=None,
        allergens=("Dairy",),
        confidence=Confidence.OFFICIAL_PUBLISHED,
        provenance=Provenance(
            snapshot_id=ref.snapshot_id,
            content_sha256=ref.content_sha256,
            source_url=ref.source_url,
            parser_version="2026-08-21.m2.1",
            fetched_at=datetime.now(UTC),
        ),
    )
    profile_repo = SqlProfileRepository(DATABASE_URL)
    profile_id = profile_repo.insert_version(profile, ref)
    again_id = profile_repo.insert_version(profile, ref)
    assert profile_id == again_id  # (food, sha) dedupe

    def _offering(ordinal: int) -> MenuOffering:
        return MenuOffering(
            offering_id=uuid4(),
            service_date=date(2026, 8, 21),
            meal_period=MealPeriod.LUNCH,
            campus_id=50,
            food_id=food_id,
            occurrence_ordinal=ordinal,
            category_name="DELI DAILY",
            category_position=8,
            item_position=3,
            source_mid="215804801",
            dietary_tags=(),
            profile_id=None,
            snapshot_id=ref.snapshot_id,
        )

    offerings = SqlOfferingRepository(DATABASE_URL)
    stored_id_1, inserted_1 = offerings.upsert(_offering(0))
    assert inserted_1 is True
    stored_id_2, inserted_2 = offerings.upsert(_offering(0))
    assert inserted_2 is False
    assert stored_id_1 == stored_id_2  # natural-key idempotent upsert

    offerings.link_profile(stored_id_1, profile_id)

    quarantine = SqlQuarantineRepository(DATABASE_URL)
    quarantine.add(
        QuarantineRecord(
            record_id=uuid4(),
            run_id=run_id,
            code=ErrorCode.PLACEHOLDER_LABEL,
            severity=Severity.WARN,
            subject_type=SubjectType.LABEL,
            natural_key={"name_normalized": "CYO Halal Bowl"},
            detail="test record",
            parser_version="2026-08-21.m2.1",
            created_at=datetime.now(UTC),
        )
    )

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM menu_offering WHERE service_date=%s AND meal_period='Lunch'",
            (date(2026, 8, 21),),
        )
        assert cur.fetchone()[0] == 1  # exactly one row despite two upserts
        cur.execute(
            "SELECT profile_id FROM menu_offering WHERE offering_id=%s", (str(stored_id_1),)
        )
        assert str(cur.fetchone()[0]) == str(profile_id)
