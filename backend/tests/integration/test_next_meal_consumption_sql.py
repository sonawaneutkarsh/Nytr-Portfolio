from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from nutrition_agent.application.ports import DuplicateNextMealConsumptionError
from nutrition_agent.db.sql_repos import (
    SqlConsumptionRepository,
    SqlNextMealConsumptionRepository,
    SqlNextMealRecommendationRepository,
)
from nutrition_agent.domain.next_meal_consumption import snapshot_next_meal_consumption
from nutrition_agent.domain.nutrition.ledger import NutritionAuthority
from tests.migration_helpers import apply_migrations
from tests.unit.test_next_meal_consumption import _recommendation

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0015_next_meal_consumption.sql"
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="scratch PostgreSQL required")


def test_migration_reapplies_with_exact_append_only_security_contract() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION.read_text(encoding="utf-8"))
        cur.execute(
            """
            SELECT has_table_privilege('authenticated','next_meal_consumption','SELECT'),
                   has_table_privilege('authenticated','next_meal_consumption','INSERT'),
                   has_table_privilege('authenticated','next_meal_consumption','UPDATE'),
                   has_table_privilege('authenticated','next_meal_consumption','DELETE'),
                   has_column_privilege(
                       'authenticated','next_meal_consumption','calories_kcal','UPDATE'
                   ),
                   relrowsecurity
            FROM pg_class
            WHERE oid='next_meal_consumption'::regclass
            """
        )
        assert cur.fetchone() == (True, True, False, False, False, True)


def test_sql_snapshot_replay_conflict_owner_isolation_and_ledger_projection() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    owner, other = uuid4(), uuid4()
    recommendation = replace(
        _recommendation(recommendation_id=uuid4(), owner=owner),
        target_policy_version_id=None,
    )
    recommendations = SqlNextMealRecommendationRepository(DATABASE_URL)
    recommendations.save(recommendation)
    repository = SqlNextMealConsumptionRepository(DATABASE_URL)
    recorded_at = datetime(2026, 9, 5, 17, tzinfo=UTC)
    entry = snapshot_next_meal_consumption(
        recommendation=recommendation,
        entry_id=uuid4(),
        client_event_id=uuid4(),
        recorded_at=recorded_at,
    )

    created = repository.save(entry)
    assert created.created is True
    assert repository.find_for_recommendation(owner, recommendation.recommendation_id) == entry
    assert repository.find_for_recommendation(other, recommendation.recommendation_id) is None
    replay = repository.save(
        replace(entry, entry_id=uuid4(), recorded_at=recorded_at + timedelta(minutes=1))
    )
    assert replay.created is False
    assert replay.entry == entry
    with pytest.raises(DuplicateNextMealConsumptionError):
        repository.save(replace(entry, entry_id=uuid4(), client_event_id=uuid4()))

    evidence = SqlConsumptionRepository(DATABASE_URL).list_eaten_evidence(
        owner,
        datetime(2026, 9, 5, tzinfo=UTC),
        datetime(2026, 9, 6, tzinfo=UTC),
    )
    assert len(evidence) == 1
    assert evidence[0].source_system == "next_meal"
    assert evidence[0].authority is NutritionAuthority.OFFICIAL
    assert evidence[0].calories_kcal == entry.calories_kcal
    assert evidence[0].protein_g == entry.protein_g

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(owner),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE next_meal_consumption SET calories_kcal=999 WHERE entry_id=%s",
                (entry.entry_id,),
            )
        conn.rollback()
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(owner),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM next_meal_consumption WHERE entry_id=%s", (entry.entry_id,))
        conn.rollback()


def test_database_rejects_cross_owner_parent_and_nonfinite_nutrition() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    owner, other = uuid4(), uuid4()
    recommendation = replace(
        _recommendation(recommendation_id=uuid4(), owner=owner),
        target_policy_version_id=None,
    )
    SqlNextMealRecommendationRepository(DATABASE_URL).save(recommendation)
    entry = snapshot_next_meal_consumption(
        recommendation=recommendation,
        entry_id=uuid4(),
        client_event_id=uuid4(),
        recorded_at=datetime(2026, 9, 5, 18, tzinfo=UTC),
    )
    repository = SqlNextMealConsumptionRepository(DATABASE_URL)
    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.InsufficientPrivilege)):
        repository.save(
            replace(
                entry,
                entry_id=uuid4(),
                user_id=other,
                client_event_id=uuid4(),
            )
        )

    second = replace(
        _recommendation(recommendation_id=uuid4(), owner=owner),
        target_policy_version_id=None,
    )
    SqlNextMealRecommendationRepository(DATABASE_URL).save(second)
    second_entry = snapshot_next_meal_consumption(
        recommendation=second,
        entry_id=uuid4(),
        client_event_id=uuid4(),
        recorded_at=datetime(2026, 9, 5, 19, tzinfo=UTC),
    )
    repository.save(second_entry)
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO next_meal_consumption
                SELECT %s,user_id,recommendation_id,%s,local_date,timezone,recorded_at,
                       state,recommendation_artifact_sha256,next_meal_policy_version,
                       meal_context,menu_period,candidate_id,item_name,serving_description,
                       configuration_summary,nutrition_authority,nutrition_confidence,
                       'NaN'::numeric,protein_g,unknown_nutrients,selected_candidate_jsonb,
                       selected_candidate_sha256
                FROM next_meal_consumption WHERE entry_id=%s
                """,
                (uuid4(), uuid4(), second_entry.entry_id),
            )
