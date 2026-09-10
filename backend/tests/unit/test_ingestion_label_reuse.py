from __future__ import annotations

import html
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from nutrition_agent.application.ingest_stacks import IngestStacksUseCase
from nutrition_agent.application.ports import (
    AcceptedMenuPageKey,
    AcceptedMenuPageObservation,
    IngestCommand,
    MenuPageValidationState,
    ValidatedMenuPage,
)
from nutrition_agent.application.stacks_refresh import (
    BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES,
    CANARY_MAX_UNIQUE_LABEL_FETCHES,
    RefreshAvailableStacksWindowUseCase,
    StacksRefreshPlanner,
    StacksRefreshScope,
    StacksRefreshStatus,
)
from nutrition_agent.db.in_memory_repos import InMemoryMenuPageVersionRepository
from nutrition_agent.domain.stacks.entities import MealPeriod, NutritionSourceState
from nutrition_agent.domain.stacks.ingestion import RunStatus
from nutrition_agent.infrastructure.http_transport import TransportError
from nutrition_agent.infrastructure.snapshot_store import RawPage
from tests.conftest import FIXTURE_DIR, make_use_case

TODAY = date(2026, 8, 21)
NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
STANDARD_LABEL = (FIXTURE_DIR / "nutrition_label_standard_roast_chicken.html").read_bytes()
PLACEHOLDER_LABEL = (FIXTURE_DIR / "nutrition_label_placeholder_halal_bowl.html").read_bytes()
INCOMPLETE_LABEL = (FIXTURE_DIR / "retained_2026-08-27_cyo_chicken_sandwich.html").read_bytes()
STANDARD_NAME = "#7 Roast Chicken & Provolone"


def _format_date(value: date) -> str:
    return f"{value.month}/{value.day}/{value.year % 100:02d}"


class SyntheticWindowSource:
    def __init__(
        self,
        *,
        default_name: str = STANDARD_NAME,
        default_mid: str = "9001",
        default_label: bytes | Exception = STANDARD_LABEL,
    ) -> None:
        self.window_start = TODAY
        self.fetched_at = NOW
        self.default_items = ((default_name, default_mid),)
        self.items_by_target: dict[tuple[date, MealPeriod], tuple[tuple[str, str], ...]] = {}
        self.labels: dict[str, bytes | Exception] = {default_mid: default_label}
        self.extra_markup_by_target: dict[tuple[date, MealPeriod], str] = {}
        self.menu_calls: list[tuple[date, MealPeriod]] = []
        self.label_calls: list[str] = []

    def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
        self.menu_calls.append((service_date, meal_period))
        items = self.items_by_target.get((service_date, meal_period), self.default_items)
        dates = tuple(self.window_start + timedelta(days=offset) for offset in range(7))
        date_options = "".join(
            f'<option value="{_format_date(value)}"'
            f"{' selected' if value == service_date else ''}>{value.isoformat()}</option>"
            for value in dates
        )
        meal_options = "".join(
            f'<option value="{period.value}"'
            f"{' selected' if period is meal_period else ''}>{period.value}</option>"
            for period in MealPeriod
        )
        item_rows = "".join(
            '<div class="menu-items daily-menu-item" '
            f'data-menu-item-name="{html.escape(name)}">'
            f'<a class="daily-menu-item__link" href="nutrition-label.cfm?mid={mid}">'
            f"{html.escape(name)}</a></div>"
            for name, mid in items
        )
        body = (
            f'<select id="selMenuDate">{date_options}</select>'
            f'<select id="selMeal">{meal_options}</select>'
            '<select id="selCampus"><option value="50" selected>Stacks</option></select>'
            '<details><summary><span class="category-name">TEST</span></summary>'
            f'<div class="category-items">{item_rows}</div></details>'
            f"{self.extra_markup_by_target.get((service_date, meal_period), '')}"
        ).encode()
        return RawPage(
            source_url="https://source.test/daily-menu.cfm",
            method="POST",
            request_params={
                "selMenuDate": _format_date(service_date),
                "selMeal": meal_period.value,
                "selCampus": "50",
            },
            body=body,
            http_status=200,
            fetched_at=self.fetched_at,
        )

    def fetch_label(self, mid_instance: str) -> RawPage:
        self.label_calls.append(mid_instance)
        response = self.labels[mid_instance]
        if isinstance(response, Exception):
            raise response
        return RawPage(
            source_url=f"https://source.test/nutrition-label.cfm?mid={mid_instance}",
            method="GET",
            request_params={"mid": mid_instance},
            body=response,
            http_status=200,
            fetched_at=self.fetched_at,
        )


class MemoryCoverage:
    def __init__(self, pages: InMemoryMenuPageVersionRepository) -> None:
        self._pages = pages

    def accepted_pages(
        self,
        *,
        campus_id: int,
        start_date: date,
        end_date: date,
    ) -> tuple[AcceptedMenuPageObservation, ...]:
        latest: dict[AcceptedMenuPageKey, datetime] = {}
        for page in self._pages.page_versions.values():
            if page.campus_id != campus_id or not start_date <= page.service_date <= end_date:
                continue
            key = AcceptedMenuPageKey(page.service_date, page.meal_period, page.campus_id)
            latest[key] = max(latest.get(key, page.accepted_at), page.accepted_at)
        return tuple(
            AcceptedMenuPageObservation(key=key, accepted_at=latest[key]) for key in sorted(latest)
        )


def _new_use_case(
    source: SyntheticWindowSource,
    tmp_path: Path,
) -> tuple[IngestStacksUseCase, object]:
    return make_use_case(source=source, snapshot_root=tmp_path)


def test_accepted_exact_label_is_reused_across_date_and_meal_period(
    tmp_path: Path,
) -> None:
    source = SyntheticWindowSource()
    first, deps = _new_use_case(source, tmp_path)
    first.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))

    second = IngestStacksUseCase(replace(deps, source=source))
    result = second.execute(IngestCommand(TODAY + timedelta(days=1), (MealPeriod.DINNER,)))

    assert result.status is RunStatus.PERSISTED
    assert source.label_calls == ["9001"]
    assert result.stats["labels_fetched"] == 0
    assert result.stats["labels_reused"] == 1


def test_changed_source_mid_for_same_name_forces_a_new_label_fetch(tmp_path: Path) -> None:
    source = SyntheticWindowSource(default_mid="9001")
    source.labels["9002"] = STANDARD_LABEL.replace(
        b"Calories:</strong> 600", b"Calories:</strong> 500", 1
    )
    first, deps = _new_use_case(source, tmp_path)
    first.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))
    source.items_by_target[(TODAY + timedelta(days=1), MealPeriod.LUNCH)] = (
        (STANDARD_NAME, "9002"),
    )

    second = IngestStacksUseCase(replace(deps, source=source))
    result = second.execute(IngestCommand(TODAY + timedelta(days=1), (MealPeriod.LUNCH,)))

    assert result.status is RunStatus.PERSISTED
    assert source.label_calls == ["9001", "9002"]
    assert result.stats["labels_fetched"] == 1
    assert len(deps.profiles.profiles) == 2


@pytest.mark.parametrize(
    ("label", "expected_state"),
    (
        (PLACEHOLDER_LABEL, NutritionSourceState.SOURCE_PLACEHOLDER),
        (INCOMPLETE_LABEL, NutritionSourceState.SOURCE_INCOMPLETE),
    ),
)
def test_accepted_non_profile_state_does_not_suppress_revalidation(
    tmp_path: Path,
    label: bytes,
    expected_state: NutritionSourceState,
) -> None:
    source = SyntheticWindowSource(
        default_name="CYO Halal Bowl",
        default_mid="9101",
        default_label=label,
    )
    first, deps = _new_use_case(source, tmp_path)
    first.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))

    second = IngestStacksUseCase(replace(deps, source=source))
    result = second.execute(IngestCommand(TODAY + timedelta(days=1), (MealPeriod.LUNCH,)))

    assert result.status is RunStatus.PERSISTED
    assert source.label_calls == ["9101", "9101"]
    assert result.stats["labels_fetched"] == 1
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    assert all(
        pin.nutrition_source_state is expected_state and pin.profile_id is None
        for pins in pages.memberships.values()
        for pin in pins
    )


def test_newest_non_profile_pin_blocks_fallback_to_older_profile(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    first, deps = _new_use_case(source, tmp_path)
    first.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    original = next(iter(pages.page_versions.values()))
    original_prepared = original.offerings[0]
    assert original_prepared.nutrition_snapshot is not None
    next_date = TODAY + timedelta(days=1)
    pages.persist_validated_page(
        ValidatedMenuPage(
            page_version_id=uuid4(),
            service_date=next_date,
            meal_period=MealPeriod.LUNCH,
            campus_id=50,
            snapshot_id=original.snapshot_id,
            ingestion_run_id=uuid4(),
            validation_state=MenuPageValidationState.VALIDATED_NONEMPTY,
            accepted_at=NOW + timedelta(hours=1),
            parser_version=original.parser_version,
            offerings=(
                replace(
                    original_prepared,
                    offering=replace(
                        original_prepared.offering,
                        offering_id=uuid4(),
                        service_date=next_date,
                    ),
                    profile=None,
                    profile_snapshot=None,
                    nutrition_source_state=NutritionSourceState.SOURCE_INCOMPLETE,
                ),
            ),
        )
    )

    third = IngestStacksUseCase(replace(deps, source=source))
    result = third.execute(IngestCommand(TODAY + timedelta(days=2), (MealPeriod.LUNCH,)))

    assert result.status is RunStatus.PERSISTED
    assert source.label_calls == ["9001", "9001"]
    assert result.stats["labels_fetched"] == 1


def test_failed_changed_label_keeps_prior_accepted_authority(tmp_path: Path) -> None:
    source = SyntheticWindowSource(default_mid="9201")
    first, deps = _new_use_case(source, tmp_path)
    first.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    before = dict(pages.page_versions)

    source.items_by_target[(TODAY + timedelta(days=1), MealPeriod.LUNCH)] = (
        (STANDARD_NAME, "9202"),
    )
    source.labels["9202"] = TransportError("offline injected label failure")
    second = IngestStacksUseCase(replace(deps, source=source))
    result = second.execute(IngestCommand(TODAY + timedelta(days=1), (MealPeriod.LUNCH,)))

    assert result.status is RunStatus.PARTIAL_FAILURE
    assert result.quarantines_by_code == {"transport_failure": 1}
    assert pages.page_versions == before
    assert source.label_calls == ["9201", "9202"]


def test_full_window_fetches_twenty_one_menus_but_one_shared_label(
    tmp_path: Path,
) -> None:
    source = SyntheticWindowSource()
    use_case, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    refresh = RefreshAvailableStacksWindowUseCase(
        StacksRefreshPlanner(MemoryCoverage(pages), deps.clock),
        use_case,
    )

    result = refresh.execute()

    assert result.status is StacksRefreshStatus.PERSISTED
    assert len(result.pages) == 21
    assert len(source.menu_calls) == 21
    assert source.label_calls == ["9001"]
    assert len(pages.page_versions) == 21
    assert len(deps.profiles.profiles) == 1
    assert Counter(page.report.stats["labels_fetched"] for page in result.pages) == {
        0: 20,
        1: 1,
    }

    # A same-day process rerun probes the source window once, reuses the exact
    # accepted label, and does not repeat the other twenty page requests.
    rerun = RefreshAvailableStacksWindowUseCase(
        StacksRefreshPlanner(MemoryCoverage(pages), deps.clock),
        IngestStacksUseCase(replace(deps, source=source)),
    ).execute()
    assert rerun.status is StacksRefreshStatus.PERSISTED
    assert len(rerun.pages) == 1
    assert len(source.menu_calls) == 22
    assert source.label_calls == ["9001"]
    assert len(deps.profiles.profiles) == 1


def test_canary_label_cap_never_accepts_truncated_pages(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    lunch_items = tuple(
        (f"Canary item {index}", str(9100 + index))
        for index in range(CANARY_MAX_UNIQUE_LABEL_FETCHES + 1)
    )
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = lunch_items
    source.items_by_target[(TODAY, MealPeriod.DINNER)] = (("Canary dinner item", "9999"),)
    source.labels.update({mid: STANDARD_LABEL for _, mid in (*lunch_items, ("dinner", "9999"))})
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    refresh = RefreshAvailableStacksWindowUseCase(
        StacksRefreshPlanner(MemoryCoverage(pages), deps.clock),
        IngestStacksUseCase(
            replace(
                deps,
                source=source,
                max_unique_label_fetches=CANARY_MAX_UNIQUE_LABEL_FETCHES,
            )
        ),
    )

    result = refresh.execute(scope=StacksRefreshScope.CANARY)

    assert result.status is StacksRefreshStatus.PARTIAL_FAILURE
    assert result.failure_reason == (
        "canary unique-label fetch cap reached; affected pages were not accepted"
    )
    assert source.menu_calls == [
        (TODAY, MealPeriod.BREAKFAST),
        (TODAY, MealPeriod.LUNCH),
        (TODAY, MealPeriod.DINNER),
    ]
    assert source.label_calls == [
        "9001",
        *[mid for _, mid in lunch_items[: CANARY_MAX_UNIQUE_LABEL_FETCHES - 1]],
    ]
    assert (
        sum(page.report.stats["unique_label_requests"] for page in result.pages)
        == CANARY_MAX_UNIQUE_LABEL_FETCHES
    )
    assert sum(page.report.stats["label_fetch_cap_reached"] for page in result.pages) == 2
    assert (
        sum(
            page.report.quarantines_by_code.get("label_fetch_limit_exceeded", 0)
            for page in result.pages
        )
        == 2
    )
    assert {(page.service_date, page.meal_period) for page in pages.page_versions.values()} == {
        (TODAY, MealPeriod.BREAKFAST)
    }


def test_canary_reuse_does_not_consume_additional_label_allowance(
    tmp_path: Path,
) -> None:
    source = SyntheticWindowSource()
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    refresh = RefreshAvailableStacksWindowUseCase(
        StacksRefreshPlanner(MemoryCoverage(pages), deps.clock),
        IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)),
    )

    result = refresh.execute(scope=StacksRefreshScope.CANARY)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert len(result.pages) == 3
    assert source.label_calls == ["9001"]
    assert sum(page.report.stats["unique_label_requests"] for page in result.pages) == 1
    assert sum(page.report.stats["labels_reused"] for page in result.pages) == 2
    assert len(pages.page_versions) == 3


def test_partial_page_label_evidence_resumes_without_accepting_truncated_page(
    tmp_path: Path,
) -> None:
    source = SyntheticWindowSource()
    items = (("Partial one", "9301"), ("Partial two", "9302"))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = items
    source.labels.update({mid: STANDARD_LABEL for _, mid in items})
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)

    first = IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    assert first.status is RunStatus.PARTIAL_FAILURE
    assert source.label_calls == ["9301"]
    assert len(deps.profiles.profiles) == 1
    assert pages.page_versions == {}
    assert len(pages.label_observations) == 1

    second = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    assert second.status is RunStatus.PERSISTED
    assert second.stats["labels_reused"] == 1
    assert source.label_calls == ["9301", "9302"]
    assert len(pages.page_versions) == 1
    assert len(pages.label_observations) == 2


@pytest.mark.parametrize(
    ("service_date", "meal_period", "replacement_mid"),
    (
        (TODAY + timedelta(days=1), MealPeriod.LUNCH, "9301"),
        (TODAY, MealPeriod.DINNER, "9301"),
        (TODAY, MealPeriod.LUNCH, "9303"),
    ),
)
def test_partial_label_evidence_cannot_cross_page_or_mid_identity(
    tmp_path: Path,
    service_date: date,
    meal_period: MealPeriod,
    replacement_mid: str,
) -> None:
    source = SyntheticWindowSource()
    original_items = (("Partial one", "9301"), ("Partial two", "9302"))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = original_items
    source.labels.update({mid: STANDARD_LABEL for _, mid in original_items})
    source.labels["9303"] = STANDARD_LABEL
    _, deps = _new_use_case(source, tmp_path)
    IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    target_items = (("Partial one", replacement_mid),)
    source.items_by_target[(service_date, meal_period)] = target_items
    result = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(service_date, (meal_period,))
    )

    assert result.status is RunStatus.PERSISTED
    assert result.stats["labels_reused"] == 0
    assert source.label_calls == ["9301", replacement_mid]


def test_parser_change_revalidates_partial_label_evidence(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    items = (("Partial one", "9301"), ("Partial two", "9302"))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = items
    source.labels.update({mid: STANDARD_LABEL for _, mid in items})
    _, deps = _new_use_case(source, tmp_path)
    IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    result = IngestStacksUseCase(
        replace(
            deps,
            source=source,
            parser_version="2026-08-31.resume-test",
            max_unique_label_fetches=1,
        )
    ).execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))

    assert result.status is RunStatus.PARTIAL_FAILURE
    assert result.stats["labels_reused"] == 0
    assert source.label_calls == ["9301", "9301"]


def test_changed_menu_bytes_reuse_only_unchanged_exact_occurrences(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    items = (("Partial one", "9351"), ("Partial two", "9352"))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = items
    source.labels.update({mid: STANDARD_LABEL for _, mid in items})
    _, deps = _new_use_case(source, tmp_path)
    IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )
    source.extra_markup_by_target[(TODAY, MealPeriod.LUNCH)] = "<!-- source page revision -->"

    result = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    assert result.status is RunStatus.PERSISTED
    assert result.stats["labels_reused"] == 1
    assert source.label_calls == ["9351", "9352"]


def test_malformed_label_never_becomes_resumable_authority(tmp_path: Path) -> None:
    malformed = STANDARD_LABEL.replace(
        b'<div class="fact-amount">20g</div>',
        b"<div>lots</div>",
    )
    source = SyntheticWindowSource(default_mid="9401", default_label=malformed)
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)

    first = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )
    assert first.status is RunStatus.PARTIAL_FAILURE
    assert pages.label_observations == {}
    source.labels["9401"] = STANDARD_LABEL
    second = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    assert second.status is RunStatus.PERSISTED
    assert len(pages.label_observations) == 1
    assert source.label_calls == ["9401", "9401"]


def test_partial_non_profile_evidence_is_reused_only_for_same_page(tmp_path: Path) -> None:
    source = SyntheticWindowSource(
        default_name="CYO Halal Bowl",
        default_mid="9501",
        default_label=PLACEHOLDER_LABEL,
    )
    items = (("CYO Halal Bowl", "9501"), (STANDARD_NAME, "9502"))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = items
    source.labels["9502"] = STANDARD_LABEL
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)

    first = IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )
    second = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    assert first.status is RunStatus.PARTIAL_FAILURE
    assert second.status is RunStatus.PERSISTED
    assert source.label_calls == ["9501", "9502"]
    accepted_pins = next(iter(pages.memberships.values()))
    assert accepted_pins[0].nutrition_source_state is NutritionSourceState.SOURCE_PLACEHOLDER
    assert accepted_pins[0].profile_id is None


def test_resumable_observation_is_idempotent_after_page_completion(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    items = (("Partial one", "9601"), ("Partial two", "9602"))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = items
    source.labels.update({mid: STANDARD_LABEL for _, mid in items})
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)

    IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=1)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )
    IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )
    observation_ids = set(pages.label_observations)
    third = IngestStacksUseCase(replace(deps, source=source)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )

    assert third.status is RunStatus.PERSISTED
    assert set(pages.label_observations) == observation_ids
    assert len(observation_ids) == 2
    assert source.label_calls == ["9601", "9602"]


def test_aug31_shaped_resume_completes_lunch_and_preserves_dinner_budget(
    tmp_path: Path,
) -> None:
    source = SyntheticWindowSource()
    lunch_items = tuple((f"Lunch item {index}", str(10000 + index)) for index in range(99))
    dinner_items = tuple((f"Dinner item {index}", str(20000 + index)) for index in range(59))
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = lunch_items
    source.items_by_target[(TODAY, MealPeriod.DINNER)] = dinner_items
    source.labels.update({mid: STANDARD_LABEL for _, mid in (*lunch_items, *dinner_items)})
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)

    first = IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=94)).execute(
        IngestCommand(TODAY, (MealPeriod.LUNCH,))
    )
    resumed = IngestStacksUseCase(replace(deps, source=source, max_unique_label_fetches=125))
    lunch = resumed.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))
    dinner = resumed.execute(IngestCommand(TODAY, (MealPeriod.DINNER,)))

    assert first.status is RunStatus.PARTIAL_FAILURE
    assert len(pages.label_observations) == 158
    assert lunch.status is RunStatus.PERSISTED
    assert lunch.stats["labels_reused"] == 94
    assert lunch.stats["labels_fetched"] == 5
    assert dinner.status is RunStatus.PERSISTED
    assert dinner.stats["labels_fetched"] == 59
    assert len(source.label_calls) == 158
    assert {(page.service_date, page.meal_period) for page in pages.page_versions.values()} == {
        (TODAY, MealPeriod.LUNCH),
        (TODAY, MealPeriod.DINNER),
    }


def test_bootstrap_cap_can_complete_observed_current_day_volume(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    lunch_items = ((STANDARD_NAME, "9001"),) + tuple(
        (f"Lunch bootstrap item {index}", str(10000 + index)) for index in range(65)
    )
    dinner_items = tuple(
        (f"Dinner bootstrap item {index}", str(11000 + index)) for index in range(59)
    )
    source.items_by_target[(TODAY, MealPeriod.LUNCH)] = lunch_items
    source.items_by_target[(TODAY, MealPeriod.DINNER)] = dinner_items
    source.labels.update({mid: STANDARD_LABEL for _, mid in (*lunch_items, *dinner_items)})
    _, deps = _new_use_case(source, tmp_path)
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    use_case = IngestStacksUseCase(
        replace(
            deps,
            source=source,
            max_unique_label_fetches=BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES,
        )
    )

    reports = tuple(use_case.execute(IngestCommand(TODAY, (period,))) for period in MealPeriod)

    assert all(report.status is RunStatus.PERSISTED for report in reports)
    assert len(source.label_calls) == BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES
    assert len(set(source.label_calls)) == BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES
    assert sum(report.stats["labels_reused"] for report in reports) >= 1
    assert len(pages.page_versions) == 3


def test_failed_shared_label_is_attempted_only_once_for_the_full_batch(
    tmp_path: Path,
) -> None:
    source = SyntheticWindowSource(
        default_label=TransportError("offline injected shared-label failure")
    )
    use_case, deps = _new_use_case(source, tmp_path)
    refresh = RefreshAvailableStacksWindowUseCase(
        StacksRefreshPlanner(MemoryCoverage(deps.pages), deps.clock),
        use_case,
    )

    result = refresh.execute()

    assert result.status is StacksRefreshStatus.PARTIAL_FAILURE
    assert len(result.pages) == 21
    assert len(source.menu_calls) == 21
    assert source.label_calls == ["9001"]
    assert (
        sum(page.report.quarantines_by_code.get("transport_failure", 0) for page in result.pages)
        == 21
    )


def test_profile_older_than_source_window_is_revalidated(tmp_path: Path) -> None:
    source = SyntheticWindowSource()
    first, deps = _new_use_case(source, tmp_path)
    first.execute(IngestCommand(TODAY, (MealPeriod.LUNCH,)))

    later = NOW + timedelta(days=8)
    deps.clock.value = later
    source.window_start = later.date()
    source.fetched_at = later
    second = IngestStacksUseCase(replace(deps, source=source))
    result = second.execute(IngestCommand(later.date(), (MealPeriod.LUNCH,)))

    assert result.status is RunStatus.PERSISTED
    assert source.label_calls == ["9001", "9001"]
    assert result.stats["labels_fetched"] == 1
