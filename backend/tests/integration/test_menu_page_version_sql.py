"""PostgreSQL proofs for M7 Step 5A accepted menu-page versioning."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.application.ports import (
    MenuPageOfferingPin,
    MenuPageValidationState,
    NutritionAuthorityKey,
    PreparedMenuPageOffering,
    ValidatedMenuPage,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryFoodRepository,
    InMemoryMenuPageVersionRepository,
    InMemoryOfferingRepository,
    InMemoryProfileRepository,
)
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    DietaryTag,
    MealPeriod,
    MenuOffering,
    NutrientKey,
    NutrientValue,
    NutritionProfile,
    NutritionSourceState,
    Provenance,
    ServingBasisKind,
)
from nutrition_agent.infrastructure.snapshot_store import SnapshotRef
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; migration test requires scratch Postgres",
)

PARSER_VERSION = "2026-08-21.m2.1"
FETCHED_AT = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)


def _unique_service_date() -> date:
    seed = uuid4().int
    return date(2100 + seed % 7000, 1 + (seed // 7000) % 12, 1 + (seed // 84000) % 28)


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _admin():
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    return psycopg.connect(DATABASE_URL)


def _insert_run(*, started_at: datetime = FETCHED_AT) -> UUID:
    run_id = uuid4()
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stacks_ingestion_run (
                run_id, status, mode, params, config_fingerprint,
                started_at, finished_at)
            VALUES (%s,'persisted','manual','{}','parser-test',%s,%s)
            """,
            (str(run_id), started_at, started_at),
        )
    return run_id


def _record_snapshot(
    run_id: UUID,
    *,
    content_sha256: str,
    fetched_at: datetime,
    source_url: str,
    method: str = "POST",
    request_params: dict[str, str] | None = None,
) -> SnapshotRef:
    from nutrition_agent.db.sql_repos import SqlSnapshotRepository

    return SqlSnapshotRepository(DATABASE_URL).record(
        SnapshotRef(
            snapshot_id=uuid4(),
            content_sha256=content_sha256,
            storage_path=f"fixture/{content_sha256}.html",
            source_url=source_url,
            method=method,
            request_params=request_params or {"selMeal": "Lunch"},
            http_status=200,
            fetched_at=fetched_at,
            byte_size=100,
            run_id=run_id,
        ),
        PARSER_VERSION,
    )


def test_sql_reusable_authority_requires_exact_recent_accepted_profile_pin() -> None:
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    _apply_migrations()
    run_id = _insert_run()
    service_date = _unique_service_date()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://menu",
    )
    source_mid = "9301"
    label_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url=f"fixture://nutrition-label.cfm?mid={source_mid}",
        method="GET",
        request_params={"mid": source_mid},
    )
    prepared = _prepared_offering(
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        number=1,
        profile_snapshot=label_snapshot,
    )
    prepared = replace(
        prepared,
        offering=replace(prepared.offering, source_mid=source_mid),
    )
    repository = SqlMenuPageVersionRepository(DATABASE_URL)
    repository.persist_validated_page(
        _page(
            run_id=run_id,
            service_date=service_date,
            menu_snapshot=menu_snapshot,
            offerings=(prepared,),
            accepted_at=FETCHED_AT,
        )
    )
    key = NutritionAuthorityKey(
        campus_id=50,
        name_normalized=prepared.food_name_normalized,
        source_mid=source_mid,
    )

    found = repository.find_reusable_nutrition_authorities(
        (key,),
        parser_version=PARSER_VERSION,
        fetched_not_before=FETCHED_AT - timedelta(days=7),
    )

    assert len(found) == 1
    assert found[0].key == key
    assert found[0].nutrition_snapshot.snapshot_id == label_snapshot.snapshot_id
    assert found[0].profile.provenance.content_sha256 == label_snapshot.content_sha256
    assert (
        repository.find_reusable_nutrition_authorities(
            (replace(key, source_mid="9302"),),
            parser_version=PARSER_VERSION,
            fetched_not_before=FETCHED_AT - timedelta(days=7),
        )
        == ()
    )
    assert (
        repository.find_reusable_nutrition_authorities(
            (replace(key, name_normalized="same name is not assumed"),),
            parser_version=PARSER_VERSION,
            fetched_not_before=FETCHED_AT - timedelta(days=7),
        )
        == ()
    )
    assert (
        repository.find_reusable_nutrition_authorities(
            (key,),
            parser_version="new-parser-version",
            fetched_not_before=FETCHED_AT - timedelta(days=7),
        )
        == ()
    )
    assert (
        repository.find_reusable_nutrition_authorities(
            (key,),
            parser_version=PARSER_VERSION,
            fetched_not_before=FETCHED_AT + timedelta(seconds=1),
        )
        == ()
    )

    # The latest accepted exact identity is authoritative. A newer
    # source-incomplete pin must not fall back to this older profile.
    later_run_id = _insert_run(started_at=FETCHED_AT + timedelta(hours=1))
    repository.persist_validated_page(
        _page(
            run_id=later_run_id,
            service_date=service_date,
            menu_snapshot=menu_snapshot,
            offerings=(
                replace(
                    prepared,
                    offering=replace(prepared.offering, offering_id=uuid4()),
                    profile=None,
                    profile_snapshot=None,
                    nutrition_source_state=NutritionSourceState.SOURCE_INCOMPLETE,
                    nutrition_snapshot=label_snapshot,
                ),
            ),
            accepted_at=FETCHED_AT + timedelta(hours=1),
        )
    )
    assert (
        repository.find_reusable_nutrition_authorities(
            (key,),
            parser_version=PARSER_VERSION,
            fetched_not_before=FETCHED_AT - timedelta(days=7),
        )
        == ()
    )


def test_sql_partial_normalized_profile_is_not_reusable_authority() -> None:
    from nutrition_agent.db.sql_repos import (
        SqlFoodRepository,
        SqlMenuPageVersionRepository,
        SqlOfferingRepository,
        SqlProfileRepository,
    )

    _apply_migrations()
    run_id = _insert_run()
    service_date = _unique_service_date()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"partial-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://partial-menu",
    )
    source_mid = "partial-9301"
    label_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"partial-label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url=f"fixture://nutrition-label.cfm?mid={source_mid}",
        method="GET",
        request_params={"mid": source_mid},
    )
    prepared = _prepared_offering(
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        number=91,
        profile_snapshot=label_snapshot,
    )
    food_id, _ = SqlFoodRepository(DATABASE_URL).upsert_by_name(
        50,
        prepared.food_name_raw,
        prepared.food_name_normalized,
    )
    offering_id, _ = SqlOfferingRepository(DATABASE_URL).upsert(
        replace(
            prepared.offering,
            food_id=food_id,
            source_mid=source_mid,
        )
    )
    assert prepared.profile is not None
    profile_id = SqlProfileRepository(DATABASE_URL).insert_version(
        replace(prepared.profile, food_id=food_id),
        label_snapshot,
    )
    SqlOfferingRepository(DATABASE_URL).link_profile(offering_id, profile_id)
    key = NutritionAuthorityKey(
        campus_id=50,
        name_normalized=prepared.food_name_normalized,
        source_mid=source_mid,
    )

    found = SqlMenuPageVersionRepository(DATABASE_URL).find_reusable_nutrition_authorities(
        (key,),
        parser_version=PARSER_VERSION,
        fetched_not_before=FETCHED_AT - timedelta(days=7),
    )

    assert found == ()
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM nutrition_profile WHERE profile_id=%s", (str(profile_id),)
        )
        assert int(cur.fetchone()[0]) == 1
        cur.execute(
            "SELECT COUNT(*) FROM menu_page_offering WHERE offering_id=%s",
            (str(offering_id),),
        )
        assert int(cur.fetchone()[0]) == 0


def _profile(food_id: UUID, snapshot: SnapshotRef, *, calories: str = "400") -> NutritionProfile:
    return NutritionProfile(
        food_id=food_id,
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
        nutrients={
            NutrientKey.CALORIES_KCAL: NutrientValue(
                value=Decimal(calories), unit="kcal", dv_percent=None
            )
        },
        unavailable_fields=(),
        extra_fields={},
        ingredients_raw="fixture ingredients",
        ingredient_components=None,
        allergens=(),
        confidence=Confidence.OFFICIAL_PUBLISHED,
        provenance=Provenance(
            snapshot_id=snapshot.snapshot_id,
            content_sha256=snapshot.content_sha256,
            source_url=snapshot.source_url,
            parser_version=PARSER_VERSION,
            fetched_at=snapshot.fetched_at,
        ),
    )


def _prepared_offering(
    *,
    service_date: date,
    menu_snapshot: SnapshotRef,
    number: int,
    profile_snapshot: SnapshotRef,
) -> PreparedMenuPageOffering:
    food_id = uuid4()
    return PreparedMenuPageOffering(
        offering=MenuOffering(
            offering_id=uuid4(),
            service_date=service_date,
            meal_period=MealPeriod.LUNCH,
            campus_id=50,
            food_id=food_id,
            occurrence_ordinal=0,
            category_name=f"CATEGORY {number}",
            category_position=number,
            item_position=number,
            source_mid=f"mid-{uuid4().hex}",
            snapshot_id=menu_snapshot.snapshot_id,
        ),
        food_name_raw=f"Food {number} {food_id}",
        food_name_normalized=f"Food {number} {food_id}",
        profile=_profile(food_id, profile_snapshot),
        profile_snapshot=profile_snapshot,
    )


def _page(
    *,
    run_id: UUID,
    service_date: date,
    menu_snapshot: SnapshotRef,
    offerings: tuple[PreparedMenuPageOffering, ...],
    accepted_at: datetime,
    page_version_id: UUID | None = None,
) -> ValidatedMenuPage:
    state = (
        MenuPageValidationState.VALIDATED_NONEMPTY
        if offerings
        else MenuPageValidationState.VALIDATED_EMPTY
    )
    return ValidatedMenuPage(
        page_version_id=page_version_id or uuid4(),
        service_date=service_date,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        snapshot_id=menu_snapshot.snapshot_id,
        ingestion_run_id=run_id,
        validation_state=state,
        accepted_at=accepted_at,
        parser_version=PARSER_VERSION,
        offerings=offerings,
    )


def _selected_page(service_date: date) -> tuple[UUID, str, int] | None:
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.page_version_id, p.validation_state, p.offering_count
            FROM menu_page_version p
            WHERE p.service_date=%s AND p.meal_period='Lunch' AND p.campus_id=50
            ORDER BY p.accepted_at DESC, p.page_version_id DESC
            LIMIT 1
            """,
            (service_date,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return UUID(str(row[0])), str(row[1]), int(row[2])


def _pin_tuple(pin: MenuPageOfferingPin) -> tuple[object, ...]:
    return (
        pin.page_version_id,
        pin.offering_id,
        pin.food_id,
        pin.profile_id,
        pin.name_normalized,
        pin.occurrence_ordinal,
        pin.category_name,
        pin.category_position,
        pin.item_position,
        pin.source_mid,
        tuple(tag.value for tag in pin.dietary_tags),
        pin.nutrition_source_state.value,
        pin.nutrition_snapshot_id,
    )


def _stored_pins(page_version_id: UUID) -> tuple[tuple[object, ...], ...]:
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT page_version_id, offering_id, food_id, profile_id,
                   name_normalized, occurrence_ordinal, category_name,
                   category_position, item_position, source_mid, dietary_tags,
                   nutrition_source_state, nutrition_snapshot_id
            FROM menu_page_offering
            WHERE page_version_id=%s
            ORDER BY name_normalized, offering_id
            """,
            (str(page_version_id),),
        )
        rows = cur.fetchall()
    return tuple(
        (
            UUID(str(row[0])),
            UUID(str(row[1])),
            UUID(str(row[2])),
            UUID(str(row[3])) if row[3] is not None else None,
            str(row[4]),
            int(row[5]),
            str(row[6]),
            int(row[7]),
            int(row[8]),
            str(row[9]),
            tuple(str(tag) for tag in (row[10] or [])),
            str(row[11]),
            UUID(str(row[12])),
        )
        for row in rows
    )


def test_menu_page_migrations_reapply_and_global_tables_have_expected_constraints() -> None:
    _apply_migrations()
    _apply_migrations()

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public'
              AND table_name IN ('menu_page_version','menu_page_offering')
            """
        )
        assert {row[0] for row in cur.fetchall()} == {
            "menu_page_version",
            "menu_page_offering",
        }
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname='public'
              AND tablename IN ('menu_page_version','menu_page_offering')
              AND rowsecurity
            """
        )
        assert cur.fetchall() == []
        for table in ("menu_page_version", "menu_page_offering"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege('authenticated', %s, %s)",
                    (table, privilege),
                )
                assert cur.fetchone()[0] is False

        cur.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid='menu_page_version'::regclass
            """
        )
        constraints = {row[0] for row in cur.fetchall()}
        assert "chk_menu_page_version_state_count" in constraints
        assert "uq_menu_page_version_source" not in constraints
        assert "uq_menu_page_version_run_page" in constraints
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='menu_page_offering'
            """
        )
        columns = {str(row[0]): (str(row[1]), str(row[2])) for row in cur.fetchall()}
        assert columns == {
            "page_version_id": ("uuid", "NO"),
            "offering_id": ("uuid", "NO"),
            "food_id": ("uuid", "NO"),
            "profile_id": ("uuid", "YES"),
            "name_normalized": ("text", "NO"),
            "occurrence_ordinal": ("integer", "NO"),
            "category_name": ("text", "NO"),
            "category_position": ("integer", "NO"),
            "item_position": ("integer", "NO"),
            "source_mid": ("text", "NO"),
            "dietary_tags": ("ARRAY", "NO"),
            "nutrition_source_state": ("text", "NO"),
            "nutrition_snapshot_id": ("uuid", "NO"),
        }
        cur.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid='menu_page_offering'::regclass
            """
        )
        offering_constraints = {str(row[0]) for row in cur.fetchall()}
        assert "chk_menu_page_offering_nutrition_state" in offering_constraints


def test_a_b_a_creates_three_observations_and_final_a_is_authoritative() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    service_date = _unique_service_date()
    repository = SqlMenuPageVersionRepository(DATABASE_URL)

    first_run = _insert_run(started_at=FETCHED_AT)
    first_a = _record_snapshot(
        first_run,
        content_sha256=f"content-a-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://a/first",
    )
    first_page = _page(
        run_id=first_run,
        service_date=service_date,
        menu_snapshot=first_a,
        offerings=(),
        accepted_at=FETCHED_AT,
    )
    repository.persist_validated_page(first_page)

    second_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=1))
    snapshot_b = _record_snapshot(
        second_run,
        content_sha256=f"content-b-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://b",
    )
    second_page = _page(
        run_id=second_run,
        service_date=service_date,
        menu_snapshot=snapshot_b,
        offerings=(),
        accepted_at=FETCHED_AT + timedelta(hours=1),
    )
    repository.persist_validated_page(second_page)

    third_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=2))
    replay_a = _record_snapshot(
        third_run,
        content_sha256=first_a.content_sha256,
        fetched_at=FETCHED_AT + timedelta(hours=2),
        source_url="fixture://a/replayed",
    )
    assert replay_a.snapshot_id == first_a.snapshot_id
    assert replay_a.fetched_at == FETCHED_AT
    third_page = _page(
        run_id=third_run,
        service_date=service_date,
        menu_snapshot=replay_a,
        offerings=(),
        accepted_at=FETCHED_AT + timedelta(hours=2),
    )
    repository.persist_validated_page(third_page)

    assert _selected_page(service_date) == (
        third_page.page_version_id,
        "validated_empty",
        0,
    )
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT snapshot_id, ingestion_run_id
            FROM menu_page_version
            WHERE service_date=%s AND meal_period='Lunch' AND campus_id=50
            ORDER BY accepted_at, page_version_id
            """,
            (service_date,),
        )
        rows = cur.fetchall()
    assert len(rows) == 3
    assert UUID(str(rows[0][0])) == first_a.snapshot_id
    assert UUID(str(rows[1][0])) == snapshot_b.snapshot_id
    assert UUID(str(rows[2][0])) == first_a.snapshot_id
    assert UUID(str(rows[2][1])) == third_run


def test_same_run_page_cannot_create_duplicate_accepted_observation() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    service_date = _unique_service_date()
    run_id = _insert_run()
    first_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"same-run-a-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://same-run/a",
    )
    second_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"same-run-b-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(seconds=1),
        source_url="fixture://same-run/b",
    )
    repository = SqlMenuPageVersionRepository(DATABASE_URL)
    repository.persist_validated_page(
        _page(
            run_id=run_id,
            service_date=service_date,
            menu_snapshot=first_snapshot,
            offerings=(),
            accepted_at=FETCHED_AT,
        )
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.persist_validated_page(
            _page(
                run_id=run_id,
                service_date=service_date,
                menu_snapshot=second_snapshot,
                offerings=(),
                accepted_at=FETCHED_AT + timedelta(seconds=1),
            )
        )


def test_snapshot_dedup_returns_canonical_id_used_by_page_acceptance() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository, SqlSnapshotRepository

    first_run = _insert_run()
    second_run = _insert_run(started_at=FETCHED_AT + timedelta(minutes=1))
    digest = f"same-{uuid4().hex}"
    first = _record_snapshot(
        first_run,
        content_sha256=digest,
        fetched_at=FETCHED_AT,
        source_url="fixture://first",
    )
    second = SqlSnapshotRepository(DATABASE_URL).record(
        SnapshotRef(
            snapshot_id=uuid4(),
            content_sha256=digest,
            storage_path="fixture/duplicate.html",
            source_url="fixture://duplicate",
            method="POST",
            request_params={"selMeal": "Lunch"},
            http_status=200,
            fetched_at=FETCHED_AT + timedelta(minutes=1),
            byte_size=100,
            run_id=second_run,
        ),
        PARSER_VERSION,
    )
    assert second.snapshot_id == first.snapshot_id

    service_date = _unique_service_date()
    page = _page(
        run_id=second_run,
        service_date=service_date,
        menu_snapshot=second,
        offerings=(),
        accepted_at=FETCHED_AT + timedelta(minutes=1),
    )
    SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(page)

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT snapshot_id FROM source_snapshot WHERE content_sha256=%s",
            (digest,),
        )
        stored = cur.fetchall()
        assert len(stored) == 1
        assert UUID(str(stored[0][0])) == first.snapshot_id
        cur.execute(
            "SELECT snapshot_id FROM menu_page_version WHERE page_version_id=%s",
            (str(page.page_version_id),),
        )
        assert UUID(str(cur.fetchone()[0])) == first.snapshot_id


def test_nonempty_page_commits_exact_membership_and_profile_snapshot() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    run_id = _insert_run()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://menu",
    )
    profile_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://label",
    )
    service_date = _unique_service_date()
    offerings = (
        _prepared_offering(
            service_date=service_date,
            menu_snapshot=menu_snapshot,
            number=1,
            profile_snapshot=profile_snapshot,
        ),
        _prepared_offering(
            service_date=service_date,
            menu_snapshot=menu_snapshot,
            number=2,
            profile_snapshot=profile_snapshot,
        ),
    )
    page = _page(
        run_id=run_id,
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        offerings=offerings,
        accepted_at=FETCHED_AT,
    )

    memory_repository = InMemoryMenuPageVersionRepository(
        foods=InMemoryFoodRepository(),
        offerings=InMemoryOfferingRepository(),
        profiles=InMemoryProfileRepository(),
    )
    memory_repository.persist_validated_page(page)

    outcome = SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(page)
    assert outcome.inserted_offerings == 2
    assert outcome.updated_offerings == 0
    assert outcome.profiles_persisted == 2

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT validation_state, offering_count, snapshot_id
            FROM menu_page_version WHERE page_version_id=%s
            """,
            (str(page.page_version_id),),
        )
        assert cur.fetchone() == (
            "validated_nonempty",
            2,
            menu_snapshot.snapshot_id,
        )
        cur.execute(
            """
            SELECT p.snapshot_id
            FROM menu_page_offering m
            JOIN nutrition_profile p ON p.profile_id=m.profile_id
            WHERE m.page_version_id=%s
            """,
            (str(page.page_version_id),),
        )
        assert {UUID(str(row[0])) for row in cur.fetchall()} == {profile_snapshot.snapshot_id}

    expected_pins = tuple(
        sorted(
            (_pin_tuple(pin) for pin in memory_repository.memberships[page.page_version_id]),
            key=lambda pin: (str(pin[4]), str(pin[1])),
        )
    )
    sql_pins = _stored_pins(page.page_version_id)
    # Store-generated food/profile UUIDs differ by repository convention; all
    # accepted source facts and the referential relationship are equivalent.
    assert tuple(pin[:2] + pin[4:] for pin in sql_pins) == tuple(
        pin[:2] + pin[4:] for pin in expected_pins
    )
    for pin in memory_repository.memberships[page.page_version_id]:
        assert memory_repository.profiles.profiles[pin.profile_id].food_id == pin.food_id
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM menu_page_offering m
            JOIN nutrition_profile p ON p.profile_id=m.profile_id
            WHERE m.page_version_id=%s AND p.food_id=m.food_id
            """,
            (str(page.page_version_id),),
        )
        assert cur.fetchone()[0] == len(sql_pins)


def test_nonempty_page_accepts_mixed_profile_and_source_incomplete_membership() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    run_id = _insert_run()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"mixed-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://mixed-menu",
    )
    profile_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"mixed-profile-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://mixed-profile",
    )
    incomplete_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"mixed-incomplete-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://mixed-incomplete",
    )
    service_date = _unique_service_date()
    profiled = _prepared_offering(
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        number=1,
        profile_snapshot=profile_snapshot,
    )
    incomplete_seed = _prepared_offering(
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        number=2,
        profile_snapshot=profile_snapshot,
    )
    incomplete = replace(
        incomplete_seed,
        profile=None,
        profile_snapshot=None,
        nutrition_source_state=NutritionSourceState.SOURCE_INCOMPLETE,
        nutrition_snapshot=incomplete_snapshot,
    )
    page = _page(
        run_id=run_id,
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        offerings=(profiled, incomplete),
        accepted_at=FETCHED_AT,
    )

    outcome = SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(page)

    assert outcome.inserted_offerings == 2
    assert outcome.profiles_persisted == 1
    pins = _stored_pins(page.page_version_id)
    assert len(pins) == page.offering_count == 2
    assert {(pin[11], pin[3] is None) for pin in pins} == {
        (NutritionSourceState.PROFILE_AVAILABLE.value, False),
        (NutritionSourceState.SOURCE_INCOMPLETE.value, True),
    }
    incomplete_pin = next(
        pin for pin in pins if pin[11] == NutritionSourceState.SOURCE_INCOMPLETE.value
    )
    assert incomplete_pin[12] == incomplete_snapshot.snapshot_id

    with _admin() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                UPDATE menu_page_offering
                SET nutrition_source_state='profile_available'
                WHERE page_version_id=%s AND profile_id IS NULL
                """,
                (str(page.page_version_id),),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                """
                UPDATE menu_page_offering
                SET nutrition_snapshot_id=%s
                WHERE page_version_id=%s AND profile_id IS NULL
                """,
                (str(uuid4()), str(page.page_version_id)),
            )


def test_validated_empty_supersedes_older_nonempty_but_preserves_both_versions() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    service_date = _unique_service_date()
    first_run = _insert_run()
    first_snapshot = _record_snapshot(
        first_run,
        content_sha256=f"menu-a-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://menu-a",
    )
    first_profile_snapshot = _record_snapshot(
        first_run,
        content_sha256=f"label-a-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://label-a",
    )
    first = _page(
        run_id=first_run,
        service_date=service_date,
        menu_snapshot=first_snapshot,
        offerings=(
            _prepared_offering(
                service_date=service_date,
                menu_snapshot=first_snapshot,
                number=1,
                profile_snapshot=first_profile_snapshot,
            ),
        ),
        accepted_at=FETCHED_AT,
    )
    repo = SqlMenuPageVersionRepository(DATABASE_URL)
    repo.persist_validated_page(first)

    second_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=1))
    second_snapshot = _record_snapshot(
        second_run,
        content_sha256=f"menu-b-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://menu-b",
    )
    second = _page(
        run_id=second_run,
        service_date=service_date,
        menu_snapshot=second_snapshot,
        offerings=(),
        accepted_at=FETCHED_AT + timedelta(hours=1),
    )
    repo.persist_validated_page(second)

    assert _selected_page(service_date) == (
        second.page_version_id,
        "validated_empty",
        0,
    )
    with _admin() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM menu_page_version WHERE service_date=%s", (service_date,))
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT COUNT(*) FROM menu_page_offering WHERE page_version_id=%s",
            (str(second.page_version_id),),
        )
        assert cur.fetchone()[0] == 0


def test_newer_unaccepted_snapshot_does_not_supersede_prior_valid_page() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    service_date = _unique_service_date()
    run_id = _insert_run()
    accepted_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"accepted-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://accepted",
    )
    accepted = _page(
        run_id=run_id,
        service_date=service_date,
        menu_snapshot=accepted_snapshot,
        offerings=(),
        accepted_at=FETCHED_AT,
    )
    SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(accepted)

    failed_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=1))
    _record_snapshot(
        failed_run,
        content_sha256=f"failed-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://failed",
    )

    assert _selected_page(service_date) == (
        accepted.page_version_id,
        "validated_empty",
        0,
    )


def test_partial_normalized_write_failure_rolls_back_every_page_write() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    run_id = _insert_run()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"rollback-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://rollback-menu",
    )
    service_date = _unique_service_date()
    valid_profile_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"rollback-label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://rollback-label",
    )
    first = _prepared_offering(
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        number=1,
        profile_snapshot=valid_profile_snapshot,
    )
    missing_profile_snapshot = SnapshotRef(
        snapshot_id=uuid4(),
        content_sha256=f"missing-{uuid4().hex}",
        storage_path="missing",
        source_url="fixture://missing",
        method="GET",
        request_params={},
        http_status=200,
        fetched_at=FETCHED_AT,
    )
    second = _prepared_offering(
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        number=2,
        profile_snapshot=missing_profile_snapshot,
    )
    page = _page(
        run_id=run_id,
        service_date=service_date,
        menu_snapshot=menu_snapshot,
        offerings=(first, second),
        accepted_at=FETCHED_AT,
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(page)

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM menu_page_version WHERE page_version_id=%s",
            (str(page.page_version_id),),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM menu_page_offering WHERE page_version_id=%s",
            (str(page.page_version_id),),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM stacks_food WHERE name_normalized IN (%s,%s)",
            (first.food_name_normalized, second.food_name_normalized),
        )
        assert cur.fetchone()[0] == 0


def test_acceptance_insert_failure_rolls_back_membership_and_new_offering() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    run_id = _insert_run()
    snapshot = _record_snapshot(
        run_id,
        content_sha256=f"acceptance-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://acceptance",
    )
    service_date = _unique_service_date()
    repo = SqlMenuPageVersionRepository(DATABASE_URL)
    original = _page(
        run_id=run_id,
        service_date=service_date,
        menu_snapshot=snapshot,
        offerings=(),
        accepted_at=FETCHED_AT,
    )
    repo.persist_validated_page(original)

    profile_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"acceptance-label-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://acceptance-label",
    )
    prepared = _prepared_offering(
        service_date=service_date,
        menu_snapshot=snapshot,
        number=99,
        profile_snapshot=profile_snapshot,
    )
    duplicate = _page(
        run_id=run_id,
        service_date=service_date,
        menu_snapshot=snapshot,
        offerings=(prepared,),
        accepted_at=FETCHED_AT,
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.persist_validated_page(duplicate)

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM menu_page_offering WHERE page_version_id=%s",
            (str(duplicate.page_version_id),),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM stacks_food WHERE name_normalized=%s",
            (prepared.food_name_normalized,),
        )
        assert cur.fetchone()[0] == 0


def test_later_page_mutates_live_row_but_preserves_page_pins_and_profile() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    first_run = _insert_run()
    first_snapshot = _record_snapshot(
        first_run,
        content_sha256=f"membership-a-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://membership-a",
    )
    first_profile_snapshot = _record_snapshot(
        first_run,
        content_sha256=f"profile-a-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://profile-a",
    )
    second_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=1))
    second_snapshot = _record_snapshot(
        second_run,
        content_sha256=f"membership-b-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://membership-b",
    )
    second_profile_snapshot = _record_snapshot(
        second_run,
        content_sha256=f"profile-b-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://profile-b",
    )
    service_date = _unique_service_date()
    first = _prepared_offering(
        service_date=service_date,
        menu_snapshot=first_snapshot,
        number=1,
        profile_snapshot=first_profile_snapshot,
    )
    first = replace(
        first,
        offering=replace(first.offering, dietary_tags=(DietaryTag.MEATLESS,)),
    )
    first_page = _page(
        run_id=first_run,
        service_date=service_date,
        menu_snapshot=first_snapshot,
        offerings=(first,),
        accepted_at=FETCHED_AT,
    )
    repository = SqlMenuPageVersionRepository(DATABASE_URL)
    repository.persist_validated_page(first_page)
    first_pins_before = _stored_pins(first_page.page_version_id)
    assert len(first_pins_before) == 1
    first_pin = first_pins_before[0]

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.profile_id, p.food_id, p.snapshot_id, p.parser_version,
                   p.serving_basis_raw, p.nutrients, p.confidence,
                   s.content_sha256, s.source_url
            FROM nutrition_profile p
            JOIN source_snapshot s ON s.snapshot_id=p.snapshot_id
            WHERE p.profile_id=%s
            """,
            (str(first_pin[3]),),
        )
        first_profile_before = cur.fetchone()
    assert first_profile_before is not None
    assert UUID(str(first_profile_before[2])) == first_profile_snapshot.snapshot_id
    assert dict(first_profile_before[5])[NutrientKey.CALORIES_KCAL.value]["value"] == "400"
    assert str(first_profile_before[7]) == first_profile_snapshot.content_sha256
    assert str(first_profile_before[8]) == first_profile_snapshot.source_url

    second = PreparedMenuPageOffering(
        offering=replace(
            first.offering,
            offering_id=uuid4(),
            category_name="UPDATED LIVE CATEGORY",
            category_position=91,
            item_position=92,
            source_mid="updated-mid",
            dietary_tags=(DietaryTag.VEGAN, DietaryTag.GLUTEN_FRIENDLY),
            snapshot_id=second_snapshot.snapshot_id,
        ),
        food_name_raw=first.food_name_raw,
        food_name_normalized=first.food_name_normalized,
        profile=_profile(
            first.offering.food_id,
            second_profile_snapshot,
            calories="525",
        ),
        profile_snapshot=second_profile_snapshot,
    )
    second_page = _page(
        run_id=second_run,
        service_date=service_date,
        menu_snapshot=second_snapshot,
        offerings=(second,),
        accepted_at=FETCHED_AT + timedelta(hours=1),
    )
    repository.persist_validated_page(second_page)

    assert _stored_pins(first_page.page_version_id) == first_pins_before
    second_pins = _stored_pins(second_page.page_version_id)
    assert len(second_pins) == 1
    second_pin = second_pins[0]
    assert first_pin[4] == first.food_name_normalized
    assert first_pin[5] == first.offering.occurrence_ordinal
    assert first_pin[6] == first.offering.category_name
    assert first_pin[7] == first.offering.category_position
    assert first_pin[8] == first.offering.item_position
    assert first_pin[9] == first.offering.source_mid
    assert first_pin[10] == (DietaryTag.MEATLESS.value,)
    assert first_pin[1] == second_pin[1]  # stable live offering identity
    assert first_pin[2] == second_pin[2]  # stable food identity
    assert first_pin[4] == second_pin[4]  # stable normalized name
    assert first_pin[5] == second_pin[5]  # stable occurrence ordinal
    assert first_pin[3] != second_pin[3]  # exact P1 vs P2 profile pins
    assert first_pin[6:] != second_pin[6:]

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT category_name, category_position, item_position, source_mid,
                   dietary_tags, snapshot_id, profile_id, food_id,
                   occurrence_ordinal
            FROM menu_offering WHERE offering_id=%s
            """,
            (str(first_pin[1]),),
        )
        live = cur.fetchone()
        assert live == (
            "UPDATED LIVE CATEGORY",
            91,
            92,
            "updated-mid",
            [DietaryTag.VEGAN.value, DietaryTag.GLUTEN_FRIENDLY.value],
            second_snapshot.snapshot_id,
            second_pin[3],
            second_pin[2],
            second_pin[5],
        )
        cur.execute(
            """
            SELECT p.profile_id, p.food_id, p.snapshot_id, p.parser_version,
                   p.serving_basis_raw, p.nutrients, p.confidence,
                   s.content_sha256, s.source_url
            FROM nutrition_profile p
            JOIN source_snapshot s ON s.snapshot_id=p.snapshot_id
            WHERE p.profile_id=%s
            """,
            (str(first_pin[3]),),
        )
        assert cur.fetchone() == first_profile_before
