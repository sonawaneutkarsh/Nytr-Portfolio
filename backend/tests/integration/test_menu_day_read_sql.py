"""Authoritative SQL menu-day reconstruction from immutable accepted pins."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.application.ports import (
    MenuPageValidationState,
    PreparedMenuPageOffering,
    ValidatedMenuPage,
)
from nutrition_agent.domain.stacks.entities import (
    DietaryTag,
    MealPeriod,
    MenuOffering,
    NutrientKey,
    NutritionProfile,
    NutritionSourceState,
)
from nutrition_agent.infrastructure.snapshot_store import SnapshotRef
from tests.integration.test_menu_page_version_sql import (
    FETCHED_AT,
    PARSER_VERSION,
    _apply_migrations,
    _insert_run,
    _profile,
    _record_snapshot,
    _unique_service_date,
)
from tests.retained_live_fixtures import retained_menu_items

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; repository test requires scratch Postgres",
)


@dataclass(frozen=True)
class _Accepted:
    page: ValidatedMenuPage
    menu_snapshot: SnapshotRef
    prepared: tuple[PreparedMenuPageOffering, ...]


def _accept_page(
    *,
    service_date: date,
    period: MealPeriod,
    fetched_at: datetime,
    names: tuple[str, ...] = (),
    calories: tuple[str, ...] = (),
    category_name: str = "CATEGORY",
    source_mid: str = "mid-original",
    dietary_tags: tuple[DietaryTag, ...] = (),
    menu_content_sha256: str | None = None,
    source_states: tuple[NutritionSourceState, ...] = (),
) -> _Accepted:
    from nutrition_agent.db.sql_repos import SqlMenuPageVersionRepository

    run_id = _insert_run(started_at=fetched_at)
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=menu_content_sha256 or f"menu-{period.value}-{uuid4().hex}",
        fetched_at=fetched_at,
        source_url=f"fixture://menu/{period.value}/{uuid4().hex}",
    )
    prepared: list[PreparedMenuPageOffering] = []
    for index, name in enumerate(names):
        food_id = uuid4()
        profile_snapshot = _record_snapshot(
            run_id,
            content_sha256=f"profile-{period.value}-{uuid4().hex}",
            fetched_at=fetched_at,
            source_url=f"fixture://profile/{period.value}/{uuid4().hex}",
        )
        profile = _profile(
            food_id,
            profile_snapshot,
            calories=calories[index] if calories else str(400 + index),
        )
        source_state = (
            source_states[index] if source_states else NutritionSourceState.PROFILE_AVAILABLE
        )
        prepared.append(
            PreparedMenuPageOffering(
                offering=MenuOffering(
                    offering_id=uuid4(),
                    service_date=service_date,
                    meal_period=period,
                    campus_id=50,
                    food_id=food_id,
                    occurrence_ordinal=index,
                    category_name=category_name,
                    category_position=index + 1,
                    item_position=index + 1,
                    source_mid=f"{source_mid}-{index}",
                    dietary_tags=dietary_tags,
                    snapshot_id=menu_snapshot.snapshot_id,
                ),
                food_name_raw=name,
                food_name_normalized=name,
                profile=(
                    profile if source_state is NutritionSourceState.PROFILE_AVAILABLE else None
                ),
                profile_snapshot=(
                    profile_snapshot
                    if source_state is NutritionSourceState.PROFILE_AVAILABLE
                    else None
                ),
                nutrition_source_state=source_state,
                nutrition_snapshot=profile_snapshot,
            )
        )
    state = (
        MenuPageValidationState.VALIDATED_NONEMPTY
        if prepared
        else MenuPageValidationState.VALIDATED_EMPTY
    )
    page = ValidatedMenuPage(
        page_version_id=uuid4(),
        service_date=service_date,
        meal_period=period,
        campus_id=50,
        snapshot_id=menu_snapshot.snapshot_id,
        ingestion_run_id=run_id,
        validation_state=state,
        accepted_at=fetched_at,
        parser_version=PARSER_VERSION,
        offerings=tuple(prepared),
    )
    SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(page)
    return _Accepted(page=page, menu_snapshot=menu_snapshot, prepared=tuple(prepared))


def _seed_other_periods(service_date: date, fetched_at: datetime) -> None:
    _accept_page(
        service_date=service_date,
        period=MealPeriod.BREAKFAST,
        fetched_at=fetched_at,
    )
    _accept_page(
        service_date=service_date,
        period=MealPeriod.DINNER,
        fetched_at=fetched_at + timedelta(seconds=1),
    )


def _digest_period(
    accepted: _Accepted,
    period: MealPeriod,
    food_ids: Mapping[tuple[str, int], UUID] | None = None,
) -> dict[str, object]:
    memberships: list[dict[str, object]] = []
    for prepared in accepted.prepared:
        offering = prepared.offering
        assert prepared.nutrition_snapshot is not None
        assert prepared.nutrition_source_state is not None
        profile_sha256 = (
            prepared.profile_snapshot.content_sha256
            if prepared.profile_snapshot is not None
            else None
        )
        memberships.append(
            {
                "category_position": offering.category_position,
                "dietary_tags": sorted(tag.value for tag in offering.dietary_tags),
                "food_id": str(
                    food_ids.get(
                        (prepared.food_name_normalized, offering.occurrence_ordinal),
                        offering.food_id,
                    )
                    if food_ids is not None
                    else offering.food_id
                ),
                "item_position": offering.item_position,
                "name_normalized": prepared.food_name_normalized,
                "nutrition_snapshot_sha256": prepared.nutrition_snapshot.content_sha256,
                "nutrition_source_state": prepared.nutrition_source_state.value,
                "occurrence_ordinal": offering.occurrence_ordinal,
                "profile_sha256": profile_sha256,
                "source_mid": offering.source_mid,
            }
        )
    return {
        "meal_period": period.value,
        "snapshot_sha256": accepted.menu_snapshot.content_sha256,
        "memberships": memberships,
    }


def test_reader_composes_cross_run_periods_and_exact_logical_day_hash() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuDayReadRepository

    service_date = _unique_service_date()
    breakfast = _accept_page(
        service_date=service_date,
        period=MealPeriod.BREAKFAST,
        fetched_at=FETCHED_AT,
        names=("Breakfast Food",),
    )
    lunch = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT + timedelta(minutes=1),
        names=("Lunch Food",),
    )
    dinner = _accept_page(
        service_date=service_date,
        period=MealPeriod.DINNER,
        fetched_at=FETCHED_AT + timedelta(minutes=2),
        names=("Dinner Food",),
    )

    repository = SqlMenuDayReadRepository(DATABASE_URL)
    first = repository.get_for_date(service_date)
    second = repository.get_for_date(service_date)
    assert first is not None
    assert second == first
    assert tuple(first.menu.periods) == tuple(MealPeriod)
    assert first.menu.fetched_at == FETCHED_AT
    assert {
        period: tuple(item.name_normalized for item in first.menu.periods[period].offerings)
        for period in MealPeriod
    } == {
        MealPeriod.BREAKFAST: ("Breakfast Food",),
        MealPeriod.LUNCH: ("Lunch Food",),
        MealPeriod.DINNER: ("Dinner Food",),
    }
    assert len(first.offering_profile_ids) == 3

    expected_payload = {
        "schema": "menu-day-snapshot.v2",
        "service_date": service_date.isoformat(),
        "periods": [
            _digest_period(
                breakfast,
                MealPeriod.BREAKFAST,
                {
                    (item.name_normalized, item.occurrence_ordinal): item.food_id
                    for item in first.menu.periods[MealPeriod.BREAKFAST].offerings
                },
            ),
            _digest_period(
                lunch,
                MealPeriod.LUNCH,
                {
                    (item.name_normalized, item.occurrence_ordinal): item.food_id
                    for item in first.menu.periods[MealPeriod.LUNCH].offerings
                },
            ),
            _digest_period(
                dinner,
                MealPeriod.DINNER,
                {
                    (item.name_normalized, item.occurrence_ordinal): item.food_id
                    for item in first.menu.periods[MealPeriod.DINNER].offerings
                },
            ),
        ],
    }
    expected = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert first.menu.snapshot_sha256 == expected
    assert first.menu.snapshot_sha256 == second.menu.snapshot_sha256


def test_reader_reconstructs_all_members_and_excludes_nonprofiles_from_planning() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuDayReadRepository
    from nutrition_agent.domain.planning.context import MealContext
    from nutrition_agent.domain.planning.eligibility import ReasonCode, offering_gate
    from nutrition_agent.domain.planning.policy import SlotPolicy

    service_date = _unique_service_date()
    accepted = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT,
        names=("Profiled", "Placeholder", "Incomplete"),
        source_states=(
            NutritionSourceState.PROFILE_AVAILABLE,
            NutritionSourceState.SOURCE_PLACEHOLDER,
            NutritionSourceState.SOURCE_INCOMPLETE,
        ),
    )

    resolved = SqlMenuDayReadRepository(
        DATABASE_URL,
        required_periods=(MealPeriod.LUNCH,),
    ).get_for_date(service_date)

    assert resolved is not None
    offerings = resolved.menu.periods[MealPeriod.LUNCH].offerings
    assert tuple(item.name_normalized for item in offerings) == (
        "Profiled",
        "Placeholder",
        "Incomplete",
    )
    assert [item.profile is not None for item in offerings] == [True, False, False]
    assert [item.nutrition_source_state for item in offerings] == [
        NutritionSourceState.PROFILE_AVAILABLE,
        NutritionSourceState.SOURCE_PLACEHOLDER,
        NutritionSourceState.SOURCE_INCOMPLETE,
    ]
    assert all(item.nutrition_snapshot_sha256 for item in offerings)
    assert len(resolved.offering_profile_ids) == 1
    policy = SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
    for item in offerings[1:]:
        rejection = offering_gate(item, policy)
        assert rejection is not None
        assert rejection.reason is ReasonCode.NO_PROFILE_LINKED

    expected_payload = {
        "schema": "menu-day-snapshot.v2",
        "service_date": service_date.isoformat(),
        "periods": [
            _digest_period(
                accepted,
                MealPeriod.LUNCH,
                {
                    (item.name_normalized, item.occurrence_ordinal): item.food_id
                    for item in offerings
                },
            )
        ],
    }
    expected = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert resolved.menu.snapshot_sha256 == expected


def test_reader_reconstructs_all_99_retained_occurrences_and_five_source_states() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import (
        SqlMenuDayReadRepository,
        SqlMenuPageVersionRepository,
    )
    from nutrition_agent.domain.planning.context import MealContext
    from nutrition_agent.domain.planning.eligibility import ReasonCode, offering_gate
    from nutrition_agent.domain.planning.policy import SlotPolicy

    service_date = _unique_service_date()
    run_id = _insert_run()
    menu_snapshot = _record_snapshot(
        run_id,
        content_sha256=f"retained-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT,
        source_url="fixture://retained/menu",
    )
    source_states = {
        "Demo Placeholder A": NutritionSourceState.SOURCE_PLACEHOLDER,
        "Demo Placeholder B": NutritionSourceState.SOURCE_PLACEHOLDER,
        "Demo Placeholder C": NutritionSourceState.SOURCE_PLACEHOLDER,
        "Demo Incomplete A": NutritionSourceState.SOURCE_INCOMPLETE,
        "Demo Incomplete B": NutritionSourceState.SOURCE_INCOMPLETE,
    }
    exact_snapshots: dict[str, SnapshotRef] = {}
    prepared: list[PreparedMenuPageOffering] = []
    for item in retained_menu_items():
        state = item.nutrition_source_state
        nutrition_snapshot = exact_snapshots.get(item.nutrition_snapshot_sha256)
        if nutrition_snapshot is None:
            nutrition_snapshot = _record_snapshot(
                run_id,
                content_sha256=item.nutrition_snapshot_sha256,
                fetched_at=FETCHED_AT,
                source_url=f"fixture://retained/label/{item.source_mid}",
            )
            exact_snapshots[item.nutrition_snapshot_sha256] = nutrition_snapshot
        food_id = uuid4()
        profile = (
            _profile(food_id, nutrition_snapshot)
            if state is NutritionSourceState.PROFILE_AVAILABLE
            else None
        )
        prepared.append(
            PreparedMenuPageOffering(
                offering=MenuOffering(
                    offering_id=uuid4(),
                    service_date=service_date,
                    meal_period=MealPeriod.LUNCH,
                    campus_id=50,
                    food_id=food_id,
                    occurrence_ordinal=item.occurrence_ordinal,
                    category_name=item.category_name,
                    category_position=item.category_position,
                    item_position=item.item_position,
                    source_mid=item.source_mid,
                    dietary_tags=item.dietary_tags,
                    snapshot_id=menu_snapshot.snapshot_id,
                ),
                food_name_raw=item.name,
                food_name_normalized=item.name,
                profile=profile,
                profile_snapshot=(
                    nutrition_snapshot if state is NutritionSourceState.PROFILE_AVAILABLE else None
                ),
                nutrition_source_state=state,
                nutrition_snapshot=nutrition_snapshot,
            )
        )
    page = ValidatedMenuPage(
        page_version_id=uuid4(),
        service_date=service_date,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        snapshot_id=menu_snapshot.snapshot_id,
        ingestion_run_id=run_id,
        validation_state=MenuPageValidationState.VALIDATED_NONEMPTY,
        accepted_at=FETCHED_AT,
        parser_version=PARSER_VERSION,
        offerings=tuple(prepared),
    )

    outcome = SqlMenuPageVersionRepository(DATABASE_URL).persist_validated_page(page)
    resolved = SqlMenuDayReadRepository(
        DATABASE_URL,
        required_periods=(MealPeriod.LUNCH,),
    ).get_for_date(service_date)

    assert outcome.inserted_offerings == 99
    assert outcome.profiles_persisted == 94
    assert resolved is not None
    offerings = resolved.menu.periods[MealPeriod.LUNCH].offerings
    assert len(offerings) == page.offering_count == 99
    assert tuple(item.name_normalized for item in offerings) == tuple(
        item.name for item in retained_menu_items()
    )
    assert len(resolved.offering_profile_ids) == 94
    nonprofiles = [item for item in offerings if item.profile is None]
    assert {item.name_normalized: item.nutrition_source_state for item in nonprofiles} == (
        source_states
    )
    assert all(item.nutrition_snapshot_sha256 for item in offerings)
    policy = SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
    for item in nonprofiles:
        rejection = offering_gate(item, policy)
        assert rejection is not None
        assert rejection.reason is ReasonCode.NO_PROFILE_LINKED


def test_latest_accepted_nonempty_wins_and_newer_unaccepted_snapshot_is_ignored() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuDayReadRepository

    service_date = _unique_service_date()
    _seed_other_periods(service_date, FETCHED_AT)
    _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT,
        names=("Old Accepted",),
    )
    newest = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT + timedelta(hours=1),
        names=("New Accepted",),
    )
    failed_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=2))
    _record_snapshot(
        failed_run,
        content_sha256=f"failed-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=2),
        source_url="fixture://failed-newer",
    )

    resolved = SqlMenuDayReadRepository(DATABASE_URL).get_for_date(service_date)
    assert resolved is not None
    lunch = resolved.menu.periods[MealPeriod.LUNCH]
    assert tuple(item.name_normalized for item in lunch.offerings) == ("New Accepted",)
    assert lunch.offerings[0].snapshot_sha256 == newest.menu_snapshot.content_sha256


def test_reader_selects_final_a_accepted_observation_after_a_b_a() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuDayReadRepository

    service_date = _unique_service_date()
    _seed_other_periods(service_date, FETCHED_AT)
    content_a = f"repeat-a-{uuid4().hex}"
    first_a = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT,
        names=("A Accepted",),
        menu_content_sha256=content_a,
    )
    _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT + timedelta(hours=1),
        names=("B Accepted",),
    )
    final_a = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT + timedelta(hours=2),
        names=("A Accepted",),
        menu_content_sha256=content_a,
    )
    assert final_a.menu_snapshot.snapshot_id == first_a.menu_snapshot.snapshot_id
    assert final_a.menu_snapshot.fetched_at == first_a.menu_snapshot.fetched_at

    resolved = SqlMenuDayReadRepository(DATABASE_URL).get_for_date(service_date)

    assert resolved is not None
    lunch = resolved.menu.periods[MealPeriod.LUNCH]
    assert tuple(item.name_normalized for item in lunch.offerings) == ("A Accepted",)
    assert lunch.offerings[0].snapshot_sha256 == content_a


def test_newer_validated_empty_supersedes_older_nonempty_without_fallback() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuDayReadRepository

    service_date = _unique_service_date()
    _seed_other_periods(service_date, FETCHED_AT)
    _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT,
        names=("Stale Lunch",),
    )
    empty = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT + timedelta(hours=1),
    )

    resolved = SqlMenuDayReadRepository(DATABASE_URL).get_for_date(service_date)
    assert resolved is not None
    lunch = resolved.menu.periods[MealPeriod.LUNCH]
    assert lunch.explicitly_empty is True
    assert lunch.offerings == ()
    assert empty.menu_snapshot.content_sha256 != ""


def test_reader_uses_immutable_pins_and_exact_profile_after_live_mutation() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import (
        SqlMenuDayReadRepository,
        SqlOfferingRepository,
        SqlProfileRepository,
    )

    service_date = _unique_service_date()
    _seed_other_periods(service_date, FETCHED_AT)
    accepted = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT,
        names=("Pinned Food",),
        calories=("400",),
        category_name="PINNED CATEGORY",
        source_mid="pinned-mid",
        dietary_tags=(DietaryTag.MEATLESS,),
    )
    original = accepted.prepared[0]

    with pytest.importorskip("psycopg").connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT offering_id, food_id, profile_id
            FROM menu_page_offering WHERE page_version_id=%s
            """,
            (str(accepted.page.page_version_id),),
        )
        offering_id, food_id, pinned_profile_id = cur.fetchone()
    offering_id = UUID(str(offering_id))
    food_id = UUID(str(food_id))
    pinned_profile_id = UUID(str(pinned_profile_id))

    later_run = _insert_run(started_at=FETCHED_AT + timedelta(hours=1))
    later_menu_snapshot = _record_snapshot(
        later_run,
        content_sha256=f"later-menu-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://later-menu",
    )
    later_profile_snapshot = _record_snapshot(
        later_run,
        content_sha256=f"later-profile-{uuid4().hex}",
        fetched_at=FETCHED_AT + timedelta(hours=1),
        source_url="fixture://later-profile",
    )
    p2: NutritionProfile = _profile(food_id, later_profile_snapshot, calories="525")
    p2_id = SqlProfileRepository(DATABASE_URL).insert_version(p2, later_profile_snapshot)
    stored_id, inserted = SqlOfferingRepository(DATABASE_URL).upsert(
        replace(
            original.offering,
            offering_id=uuid4(),
            food_id=food_id,
            category_name="MUTATED LIVE CATEGORY",
            category_position=91,
            item_position=92,
            source_mid="mutated-live-mid",
            dietary_tags=(DietaryTag.VEGAN,),
            snapshot_id=later_menu_snapshot.snapshot_id,
        )
    )
    assert inserted is False
    assert stored_id == offering_id
    SqlOfferingRepository(DATABASE_URL).link_profile(stored_id, p2_id)

    extra_food_id = uuid4()
    extra_food_name = f"Unaccepted Extra {extra_food_id}"
    with pytest.importorskip("psycopg").connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stacks_food (food_id, campus_id, name_raw, name_normalized)
            VALUES (%s,50,%s,%s)
            """,
            (str(extra_food_id), extra_food_name, extra_food_name),
        )
    SqlOfferingRepository(DATABASE_URL).upsert(
        MenuOffering(
            offering_id=uuid4(),
            service_date=service_date,
            meal_period=MealPeriod.LUNCH,
            campus_id=50,
            food_id=extra_food_id,
            occurrence_ordinal=0,
            category_name="LIVE ONLY",
            category_position=99,
            item_position=99,
            source_mid="live-only-mid",
            snapshot_id=later_menu_snapshot.snapshot_id,
        )
    )

    resolved = SqlMenuDayReadRepository(DATABASE_URL).get_for_date(service_date)
    assert resolved is not None
    lunch_items = resolved.menu.periods[MealPeriod.LUNCH].offerings
    assert len(lunch_items) == 1
    item = lunch_items[0]
    assert item.offering_id == offering_id
    assert item.food_id == food_id
    assert item.name_normalized == "Pinned Food"
    assert item.category_name == "PINNED CATEGORY"
    assert item.source_mid == "pinned-mid-0"
    assert item.dietary_tags == (DietaryTag.MEATLESS,)
    assert item.profile is not None
    assert item.profile.nutrients[NutrientKey.CALORIES_KCAL].value == Decimal("400")
    assert original.profile_snapshot is not None
    assert item.profile.provenance.snapshot_id == original.profile_snapshot.snapshot_id
    assert item.profile.provenance.content_sha256 == original.profile_snapshot.content_sha256
    assert item.profile.provenance.source_url == original.profile_snapshot.source_url
    assert item.profile.provenance.parser_version == PARSER_VERSION
    assert resolved.offering_profile_ids[str(offering_id)] == pinned_profile_id
    assert resolved.offering_profile_ids[str(offering_id)] != p2_id


def test_missing_required_period_fails_closed() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlMenuDayReadRepository

    service_date = _unique_service_date()
    lunch = _accept_page(
        service_date=service_date,
        period=MealPeriod.LUNCH,
        fetched_at=FETCHED_AT,
        names=("Only Accepted Lunch",),
    )
    repository = SqlMenuDayReadRepository(DATABASE_URL)
    assert repository.get_for_date(service_date) is None

    lunch_only = SqlMenuDayReadRepository(
        DATABASE_URL,
        required_periods=(MealPeriod.LUNCH,),
    ).get_for_date(service_date)
    assert lunch_only is not None
    assert tuple(lunch_only.menu.periods[MealPeriod.LUNCH].offerings) != ()
    assert lunch_only.menu.periods[MealPeriod.LUNCH].offerings[0].snapshot_sha256 == (
        lunch.menu_snapshot.content_sha256
    )
