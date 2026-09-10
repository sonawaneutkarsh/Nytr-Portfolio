"""PostgreSQL proofs for immutable resumable label observations."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from nutrition_agent.application.ports import (
    PageLabelAuthorityKey,
    ValidatedMenuLabelObservation,
)
from nutrition_agent.domain.stacks.entities import MealPeriod, NutritionSourceState
from nutrition_agent.infrastructure.stacks_source.constants import STACKS_CAMPUS_ID
from tests.integration.test_menu_page_version_sql import (
    FETCHED_AT,
    PARSER_VERSION,
    _admin,
    _insert_run,
    _profile,
    _record_snapshot,
    _unique_service_date,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; label evidence requires Postgres",
)


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def test_migration_0009_reapplies_with_append_only_global_posture() -> None:
    _apply_migrations()
    _apply_migrations()

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='menu_label_observation'
            """
        )
        columns = {str(row[0]): (str(row[1]), str(row[2])) for row in cur.fetchall()}
        assert columns == {
            "observation_id": ("uuid", "NO"),
            "service_date": ("date", "NO"),
            "meal_period": ("text", "NO"),
            "campus_id": ("integer", "NO"),
            "menu_snapshot_id": ("uuid", "NO"),
            "name_normalized": ("text", "NO"),
            "occurrence_ordinal": ("integer", "NO"),
            "source_mid": ("text", "NO"),
            "food_id": ("uuid", "NO"),
            "nutrition_source_state": ("text", "NO"),
            "profile_id": ("uuid", "YES"),
            "nutrition_snapshot_id": ("uuid", "NO"),
            "parser_version": ("text", "NO"),
            "ingestion_run_id": ("uuid", "NO"),
            "recorded_at": ("timestamp with time zone", "NO"),
        }
        cur.execute(
            """
            SELECT rowsecurity FROM pg_tables
            WHERE schemaname='public' AND tablename='menu_label_observation'
            """
        )
        assert cur.fetchone() == (False,)
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege('authenticated', %s, %s)",
                ("menu_label_observation", privilege),
            )
            assert cur.fetchone()[0] is False
        cur.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid='menu_label_observation'::regclass
            """
        )
        constraints = {str(row[0]) for row in cur.fetchall()}
        assert "chk_menu_label_observation_profile_state" in constraints
        assert "uq_menu_label_observation_evidence" in constraints


def test_sql_label_observation_is_idempotent_exact_page_evidence() -> None:
    from nutrition_agent.db.sql_repos import (
        SqlMenuDayReadRepository,
        SqlMenuPageVersionRepository,
    )

    _apply_migrations()
    service_date = _unique_service_date()
    run_id = _insert_run()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"resume-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://resume-menu",
        request_params={
            "selMenuDate": service_date.isoformat(),
            "selMeal": "Lunch",
            "selCampus": str(STACKS_CAMPUS_ID),
        },
    )
    source_mid = f"resume-{uuid4().hex}"
    label_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"resume-label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url=f"fixture://nutrition-label.cfm?mid={source_mid}",
        method="GET",
        request_params={"mid": source_mid},
    )
    food_id = uuid4()
    key = PageLabelAuthorityKey(
        service_date=service_date,
        meal_period=MealPeriod.LUNCH,
        campus_id=STACKS_CAMPUS_ID,
        name_normalized=f"Resume Food {food_id}",
        source_mid=source_mid,
        occurrence_ordinal=0,
    )
    observation = ValidatedMenuLabelObservation(
        observation_id=uuid4(),
        key=key,
        menu_snapshot=menu_snapshot,
        food_id=food_id,
        food_name_raw=key.name_normalized,
        nutrition_source_state=NutritionSourceState.PROFILE_AVAILABLE,
        profile=_profile(food_id, label_snapshot),
        nutrition_snapshot=label_snapshot,
        parser_version=PARSER_VERSION,
        ingestion_run_id=run_id,
        recorded_at=FETCHED_AT,
    )
    repository = SqlMenuPageVersionRepository(DATABASE_URL)

    first = repository.persist_label_observation(observation)
    second = repository.persist_label_observation(
        replace(observation, observation_id=uuid4(), recorded_at=FETCHED_AT + timedelta(minutes=1))
    )
    found = repository.find_reusable_page_label_authorities(
        (key,),
        parser_version=PARSER_VERSION,
        fetched_not_before=FETCHED_AT - timedelta(days=7),
    )

    assert first == second
    assert found == (first,)
    assert first.profile is not None
    assert first.profile.provenance.snapshot_id == label_snapshot.snapshot_id
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM menu_label_observation
            WHERE service_date=%s AND meal_period='Lunch' AND campus_id=%s
              AND source_mid=%s
            """,
            (service_date, STACKS_CAMPUS_ID, source_mid),
        )
        assert int(cur.fetchone()[0]) == 1
        cur.execute(
            """
            SELECT COUNT(*) FROM menu_page_version
            WHERE service_date=%s AND meal_period='Lunch' AND campus_id=%s
            """,
            (service_date, STACKS_CAMPUS_ID),
        )
        assert int(cur.fetchone()[0]) == 0
    assert (
        SqlMenuDayReadRepository(
            DATABASE_URL,
            required_periods=(MealPeriod.LUNCH,),
        ).get_for_date(service_date)
        is None
    )

    for changed_key in (
        replace(key, service_date=service_date + timedelta(days=1)),
        replace(key, meal_period=MealPeriod.DINNER),
        replace(key, source_mid=f"changed-{source_mid}"),
        replace(key, occurrence_ordinal=1),
    ):
        assert (
            repository.find_reusable_page_label_authorities(
                (changed_key,),
                parser_version=PARSER_VERSION,
                fetched_not_before=FETCHED_AT - timedelta(days=7),
            )
            == ()
        )
    assert (
        repository.find_reusable_page_label_authorities(
            (key,),
            parser_version="next-parser",
            fetched_not_before=FETCHED_AT - timedelta(days=7),
        )
        == ()
    )


def test_sql_same_page_non_profile_observation_is_reusable_without_profile() -> None:
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    _apply_migrations()
    service_date = _unique_service_date()
    run_id = _insert_run()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"nonprofile-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://nonprofile-menu",
    )
    source_mid = f"nonprofile-{uuid4().hex}"
    label_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"nonprofile-label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url=f"fixture://nutrition-label.cfm?mid={source_mid}",
        method="GET",
        request_params={"mid": source_mid},
    )
    food_id = uuid4()
    key = PageLabelAuthorityKey(
        service_date=service_date,
        meal_period=MealPeriod.LUNCH,
        campus_id=STACKS_CAMPUS_ID,
        name_normalized=f"Source Placeholder {food_id}",
        source_mid=source_mid,
        occurrence_ordinal=0,
    )
    repository = SqlMenuPageVersionRepository(DATABASE_URL)
    stored = repository.persist_label_observation(
        ValidatedMenuLabelObservation(
            observation_id=uuid4(),
            key=key,
            menu_snapshot=menu_snapshot,
            food_id=food_id,
            food_name_raw=key.name_normalized,
            nutrition_source_state=NutritionSourceState.SOURCE_PLACEHOLDER,
            profile=None,
            nutrition_snapshot=label_snapshot,
            parser_version=PARSER_VERSION,
            ingestion_run_id=run_id,
            recorded_at=FETCHED_AT,
        )
    )

    assert stored.profile is None
    assert stored.nutrition_source_state is NutritionSourceState.SOURCE_PLACEHOLDER
    assert repository.find_reusable_page_label_authorities(
        (key,),
        parser_version=PARSER_VERSION,
        fetched_not_before=FETCHED_AT - timedelta(days=7),
    ) == (stored,)
