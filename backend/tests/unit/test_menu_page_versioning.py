"""M7 Step 5A unit proofs for accepted menu-page versioning."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.application.ports import (
    IngestCommand,
    MenuPageValidationState,
    PreparedMenuPageOffering,
    ValidatedMenuPage,
)
from nutrition_agent.db.in_memory_repos import (
    DuplicateKeyError,
    InMemoryFoodRepository,
    InMemoryMenuPageVersionRepository,
    InMemoryOfferingRepository,
    InMemoryProfileRepository,
    InMemorySnapshotRepository,
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
from nutrition_agent.infrastructure.http_transport import TransportError
from nutrition_agent.infrastructure.snapshot_store import RawPage, SnapshotRef, SnapshotStore
from nutrition_agent.infrastructure.stacks_source.fixture_source import FixtureStacksSource
from tests.conftest import FIXTURE_DIR, SERVICE_DATE, make_use_case


def _page_repository(deps: object) -> InMemoryMenuPageVersionRepository:
    repository = deps.pages  # type: ignore[attr-defined]
    assert isinstance(repository, InMemoryMenuPageVersionRepository)
    return repository


def _fully_valid_source() -> object:
    fixture_source = FixtureStacksSource(FIXTURE_DIR)
    valid_label = (FIXTURE_DIR / "nutrition_label_standard_roast_chicken.html").read_bytes()

    class FullyValidSource:
        def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
            return fixture_source.fetch_menu_page(service_date, meal_period)

        def fetch_label(self, mid_instance: str) -> RawPage:
            return RawPage(
                source_url=f"fixture://valid-label/{mid_instance}",
                method="GET",
                request_params={"mid": mid_instance},
                body=valid_label,
                http_status=200,
                fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
            )

    return FullyValidSource()


def _profile(food_id: UUID, snapshot: SnapshotRef) -> NutritionProfile:
    return NutritionProfile(
        food_id=food_id,
        serving_basis_raw="1 SERVG",
        serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
        nutrients={
            NutrientKey.CALORIES_KCAL: NutrientValue(
                value=Decimal("400"), unit="kcal", dv_percent=None
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
            parser_version="parser.v1",
            fetched_at=snapshot.fetched_at,
        ),
    )


def test_snapshot_record_returns_one_canonical_identity_for_duplicate_content(
    tmp_path: Path,
) -> None:
    raw = RawPage(
        source_url="fixture://same",
        method="GET",
        request_params={"x": "1"},
        body=b"same immutable bytes",
        http_status=200,
        fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    store = SnapshotStore(tmp_path)
    repository = InMemorySnapshotRepository()

    first_generated = store.save(raw, UUID(int=1))
    second_generated = store.save(raw, UUID(int=2))
    assert first_generated.snapshot_id != second_generated.snapshot_id

    first = repository.record(first_generated, "parser.v1")
    second = repository.record(second_generated, "parser.v1")

    assert second.snapshot_id == first.snapshot_id
    assert len(repository.recorded) == 1


def test_in_memory_a_b_a_preserves_three_observations_and_final_a_authority(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(tmp_path)
    snapshots = InMemorySnapshotRepository()
    pages = InMemoryMenuPageVersionRepository(
        foods=InMemoryFoodRepository(),
        offerings=InMemoryOfferingRepository(),
        profiles=InMemoryProfileRepository(),
    )
    raw_a = RawPage(
        source_url="fixture://a",
        method="POST",
        request_params={"selMeal": "Lunch"},
        body=b"content A",
        http_status=200,
        fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    raw_b = replace(
        raw_a,
        source_url="fixture://b",
        body=b"content B",
        fetched_at=datetime(2026, 8, 21, 13, 0, tzinfo=UTC),
    )
    first_a = snapshots.record(store.save(raw_a, UUID(int=801)), "parser.v1")
    snapshot_b = snapshots.record(store.save(raw_b, UUID(int=802)), "parser.v1")
    replay_a = snapshots.record(
        store.save(
            replace(
                raw_a,
                source_url="fixture://a/replay",
                fetched_at=datetime(2026, 8, 21, 14, 0, tzinfo=UTC),
            ),
            UUID(int=803),
        ),
        "parser.v1",
    )
    assert replay_a.snapshot_id == first_a.snapshot_id

    observations = (
        (UUID(int=811), UUID(int=821), first_a, datetime(2026, 8, 21, 12, 0, tzinfo=UTC)),
        (UUID(int=812), UUID(int=822), snapshot_b, datetime(2026, 8, 21, 13, 0, tzinfo=UTC)),
        (UUID(int=813), UUID(int=823), replay_a, datetime(2026, 8, 21, 14, 0, tzinfo=UTC)),
    )
    for page_id, run_id, snapshot, accepted_at in observations:
        pages.persist_validated_page(
            ValidatedMenuPage(
                page_version_id=page_id,
                service_date=SERVICE_DATE,
                meal_period=MealPeriod.LUNCH,
                campus_id=50,
                snapshot_id=snapshot.snapshot_id,
                ingestion_run_id=run_id,
                validation_state=MenuPageValidationState.VALIDATED_EMPTY,
                accepted_at=accepted_at,
                parser_version="parser.v1",
                offerings=(),
            )
        )

    selected = max(
        pages.page_versions.values(),
        key=lambda page: (page.accepted_at, page.page_version_id),
    )
    assert len(pages.page_versions) == 3
    assert selected.page_version_id == UUID(int=813)
    assert selected.snapshot_id == first_a.snapshot_id


def test_in_memory_rejects_two_accepted_observations_for_same_run_page() -> None:
    pages = InMemoryMenuPageVersionRepository(
        foods=InMemoryFoodRepository(),
        offerings=InMemoryOfferingRepository(),
        profiles=InMemoryProfileRepository(),
    )
    first = ValidatedMenuPage(
        page_version_id=UUID(int=901),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        snapshot_id=UUID(int=902),
        ingestion_run_id=UUID(int=903),
        validation_state=MenuPageValidationState.VALIDATED_EMPTY,
        accepted_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        parser_version="parser.v1",
        offerings=(),
    )
    pages.persist_validated_page(first)

    with pytest.raises(DuplicateKeyError):
        pages.persist_validated_page(
            replace(
                first,
                page_version_id=UUID(int=904),
                snapshot_id=UUID(int=905),
            )
        )


def test_valid_nonempty_ingestion_creates_complete_immutable_membership(tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path, source=_fully_valid_source())
    use_case.execute(IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH]))

    pages = _page_repository(deps)
    assert len(pages.page_versions) == 1
    page = next(iter(pages.page_versions.values()))
    assert page.validation_state is MenuPageValidationState.VALIDATED_NONEMPTY
    assert page.offering_count == 22
    pins = pages.memberships[page.page_version_id]
    assert len(pins) == 22
    assert {pin.offering_id for pin in pins} == set(deps.offerings.offerings)
    first_pin = pins[0]
    live = deps.offerings.offerings[first_pin.offering_id]
    assert first_pin.food_id == live.food_id
    assert first_pin.profile_id == live.profile_id
    assert first_pin.nutrition_source_state is NutritionSourceState.PROFILE_AVAILABLE
    assert first_pin.nutrition_snapshot_id is not None
    assert first_pin.name_normalized == deps.foods.foods[live.food_id]["name_normalized"]
    assert first_pin.occurrence_ordinal == live.occurrence_ordinal
    assert first_pin.category_name == live.category_name
    assert first_pin.category_position == live.category_position
    assert first_pin.item_position == live.item_position
    assert first_pin.source_mid == live.source_mid
    assert first_pin.dietary_tags == live.dietary_tags
    assert {offering.snapshot_id for offering in deps.offerings.offerings.values()} == {
        page.snapshot_id
    }
    # Two occurrence rows share the same normalized food identity/profile.
    assert len(deps.profiles.profiles) == 21


def test_malformed_nutrition_preserves_partial_cache_without_accepting_page(
    tmp_path: Path,
) -> None:
    valid_source = _fully_valid_source()

    class OneMalformedLabelSource:
        def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
            return valid_source.fetch_menu_page(service_date, meal_period)  # type: ignore[attr-defined]

        def fetch_label(self, mid_instance: str) -> RawPage:
            if mid_instance != "900000001":
                return valid_source.fetch_label(mid_instance)  # type: ignore[attr-defined]
            standard = (FIXTURE_DIR / "nutrition_label_standard_roast_chicken.html").read_text(
                encoding="utf-8"
            )
            malformed = standard.replace(
                '<div class="fact-amount">20g</div>',
                "<div>lots</div>",
            )
            return RawPage(
                source_url=f"fixture://malformed-label/{mid_instance}",
                method="GET",
                request_params={"mid": mid_instance},
                body=malformed.encode(),
                http_status=200,
                fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
            )

    use_case, deps = make_use_case(snapshot_root=tmp_path, source=OneMalformedLabelSource())
    report = use_case.execute(
        IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH])
    )

    assert report.quarantines_by_code["nutrition_malformed"] == 1
    assert len(deps.offerings.offerings) == 22
    assert _page_repository(deps).page_versions == {}
    assert _page_repository(deps).memberships == {}


def test_valid_empty_ingestion_is_accepted_only_after_selection_validation(
    tmp_path: Path,
) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    report = use_case.execute(
        IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.BREAKFAST])
    )

    pages = _page_repository(deps)
    page = next(iter(pages.page_versions.values()))
    assert report.stats["empty_periods"] == 1
    assert page.validation_state is MenuPageValidationState.VALIDATED_EMPTY
    assert page.offering_count == 0
    assert pages.memberships[page.page_version_id] == ()


def test_empty_page_with_selection_mismatch_is_not_accepted(tmp_path: Path) -> None:
    body = (FIXTURE_DIR / "daily_menu_breakfast_empty_2026-08-21.html").read_bytes()

    class WrongSelectionEmptySource:
        def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
            return RawPage(
                source_url="fixture://wrong-empty",
                method="POST",
                request_params={"meal": meal_period.value},
                body=body,
                http_status=200,
                fetched_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
            )

        def fetch_label(self, mid_instance: str) -> RawPage:  # pragma: no cover
            raise AssertionError(mid_instance)

    use_case, deps = make_use_case(snapshot_root=tmp_path, source=WrongSelectionEmptySource())
    report = use_case.execute(
        IngestCommand(service_date=date(2026, 8, 22), meal_periods=[MealPeriod.BREAKFAST])
    )

    assert report.quarantines_by_code == {"selection_echo_mismatch": 1}
    assert report.stats["empty_periods"] == 0
    assert _page_repository(deps).page_versions == {}


def test_failed_newer_fetch_does_not_replace_prior_accepted_page(tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    command = IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH])
    use_case.execute(command)
    before = dict(_page_repository(deps).page_versions)

    class FailedSource:
        def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
            raise TransportError("newer fetch failed")

        def fetch_label(self, mid_instance: str) -> RawPage:  # pragma: no cover
            raise AssertionError(mid_instance)

    failed_use_case, _ = make_use_case(snapshot_root=tmp_path, source=FailedSource())
    failed_use_case._d = replace(failed_use_case._d, pages=deps.pages)  # noqa: SLF001
    failed_use_case.execute(command)

    assert _page_repository(deps).page_versions == before


def test_in_memory_page_persistence_rolls_back_normalized_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, deps = make_use_case()
    pages = _page_repository(deps)
    snapshot_id = UUID(int=501)
    offering = MenuOffering(
        offering_id=UUID(int=502),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        food_id=UUID(int=503),
        occurrence_ordinal=0,
        category_name="TEST",
        category_position=0,
        item_position=0,
        source_mid="mid",
        snapshot_id=snapshot_id,
    )
    profile_snapshot = SnapshotRef(
        snapshot_id=UUID(int=506),
        content_sha256="profile-sha",
        storage_path="fixture/profile.html",
        source_url="fixture://profile",
        method="GET",
        request_params={},
        http_status=200,
        fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    page = ValidatedMenuPage(
        page_version_id=UUID(int=504),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        snapshot_id=snapshot_id,
        ingestion_run_id=UUID(int=505),
        validation_state=MenuPageValidationState.VALIDATED_NONEMPTY,
        accepted_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        parser_version="parser.v1",
        offerings=(
            PreparedMenuPageOffering(
                offering=offering,
                food_name_raw="Test",
                food_name_normalized="Test",
                profile=_profile(offering.food_id, profile_snapshot),
                profile_snapshot=profile_snapshot,
            ),
        ),
    )

    def fail_after_food_write(_: MenuOffering) -> tuple[UUID, bool]:
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(deps.offerings, "upsert", fail_after_food_write)
    with pytest.raises(RuntimeError, match="injected"):
        pages.persist_validated_page(page)

    assert deps.foods.foods == {}
    assert deps.offerings.offerings == {}
    assert pages.memberships == {}
    assert pages.page_versions == {}


def test_live_offering_update_cannot_change_old_membership(tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path, source=_fully_valid_source())
    use_case.execute(IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH]))
    pages = _page_repository(deps)
    page = next(iter(pages.page_versions.values()))
    membership_before = pages.memberships[page.page_version_id]
    offering_id = membership_before[0].offering_id
    original = deps.offerings.offerings[offering_id]

    stored_id, inserted = deps.offerings.upsert(
        replace(
            original,
            category_name="UPDATED LIVE CATEGORY",
            category_position=999,
            item_position=999,
            source_mid="updated-mid",
            dietary_tags=(DietaryTag.VEGAN,),
            snapshot_id=UUID(int=999),
        )
    )
    deps.offerings.link_profile(offering_id, UUID(int=1000))

    assert inserted is False
    assert stored_id == offering_id
    assert pages.memberships[page.page_version_id] == membership_before
    assert deps.offerings.offerings[offering_id].profile_id != membership_before[0].profile_id


def test_validated_nonempty_rejects_unclassified_missing_profile() -> None:
    offering = MenuOffering(
        offering_id=UUID(int=701),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        food_id=UUID(int=702),
        occurrence_ordinal=0,
        category_name="TEST",
        category_position=0,
        item_position=0,
        source_mid="mid",
        snapshot_id=UUID(int=703),
    )

    with pytest.raises(ValueError, match="exact profile"):
        ValidatedMenuPage(
            page_version_id=UUID(int=704),
            service_date=SERVICE_DATE,
            meal_period=MealPeriod.LUNCH,
            campus_id=50,
            snapshot_id=UUID(int=703),
            ingestion_run_id=UUID(int=705),
            validation_state=MenuPageValidationState.VALIDATED_NONEMPTY,
            accepted_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
            parser_version="parser.v1",
            offerings=(
                PreparedMenuPageOffering(
                    offering=offering,
                    food_name_raw="Test",
                    food_name_normalized="Test",
                    profile=None,
                    profile_snapshot=None,
                ),
            ),
        )


def test_validated_nonempty_accepts_classified_nonprofile_membership() -> None:
    foods = InMemoryFoodRepository()
    offerings = InMemoryOfferingRepository()
    profiles = InMemoryProfileRepository()
    pages = InMemoryMenuPageVersionRepository(
        foods=foods,
        offerings=offerings,
        profiles=profiles,
    )
    nutrition_snapshot = SnapshotRef(
        snapshot_id=UUID(int=711),
        content_sha256="placeholder-label-sha",
        storage_path="fixture/placeholder.html",
        source_url="fixture://placeholder",
        method="GET",
        request_params={"mid": "placeholder-mid"},
        http_status=200,
        fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    offering = MenuOffering(
        offering_id=UUID(int=712),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        food_id=UUID(int=713),
        occurrence_ordinal=0,
        category_name="TEST",
        category_position=0,
        item_position=0,
        source_mid="placeholder-mid",
        snapshot_id=UUID(int=714),
    )
    page = ValidatedMenuPage(
        page_version_id=UUID(int=715),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        snapshot_id=UUID(int=714),
        ingestion_run_id=UUID(int=716),
        validation_state=MenuPageValidationState.VALIDATED_NONEMPTY,
        accepted_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        parser_version="parser.v1",
        offerings=(
            PreparedMenuPageOffering(
                offering=offering,
                food_name_raw="Placeholder",
                food_name_normalized="Placeholder",
                profile=None,
                profile_snapshot=None,
                nutrition_source_state=NutritionSourceState.SOURCE_PLACEHOLDER,
                nutrition_snapshot=nutrition_snapshot,
            ),
        ),
    )

    outcome = pages.persist_validated_page(page)

    pin = pages.memberships[page.page_version_id][0]
    assert outcome.profiles_persisted == 0
    assert pin.profile_id is None
    assert pin.nutrition_source_state is NutritionSourceState.SOURCE_PLACEHOLDER
    assert pin.nutrition_snapshot_id == nutrition_snapshot.snapshot_id


def test_source_state_and_profile_must_be_consistent() -> None:
    snapshot = SnapshotRef(
        snapshot_id=UUID(int=721),
        content_sha256="label-sha",
        storage_path="fixture/label.html",
        source_url="fixture://label",
        method="GET",
        request_params={},
        http_status=200,
        fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    offering = MenuOffering(
        offering_id=UUID(int=722),
        service_date=SERVICE_DATE,
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        food_id=UUID(int=723),
        occurrence_ordinal=0,
        category_name="TEST",
        category_position=0,
        item_position=0,
        source_mid="mid",
        snapshot_id=UUID(int=724),
    )

    with pytest.raises(ValueError, match="cannot carry a profile"):
        PreparedMenuPageOffering(
            offering=offering,
            food_name_raw="Test",
            food_name_normalized="Test",
            profile=_profile(offering.food_id, snapshot),
            profile_snapshot=snapshot,
            nutrition_source_state=NutritionSourceState.SOURCE_INCOMPLETE,
            nutrition_snapshot=snapshot,
        )
