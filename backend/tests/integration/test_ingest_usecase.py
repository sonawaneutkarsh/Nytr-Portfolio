"""Integration tests: ingestion lifecycle against fixtures (zero network)."""

from __future__ import annotations

import dataclasses
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from nutrition_agent.application.ingest_stacks import IngestStacksUseCase
from nutrition_agent.application.ports import IngestCommand
from nutrition_agent.db.in_memory_repos import InMemoryMenuPageVersionRepository
from nutrition_agent.domain.stacks.entities import MealPeriod, NutritionSourceState
from nutrition_agent.domain.stacks.ingestion import RunStatus
from nutrition_agent.infrastructure.http_transport import TransportError
from tests.conftest import SERVICE_DATE, make_use_case

# The public mirror ships only minimal fabricated dining fixtures, so the
# multi-offering ingestion lifecycle cannot be exercised from them. Point
# STACKS_INSTITUTIONAL_FIXTURES at a directory of provider-shaped pages to run
# these end-to-end assertions locally.
pytestmark = pytest.mark.skipif(
    not os.environ.get("STACKS_INSTITUTIONAL_FIXTURES"),
    reason="STACKS_INSTITUTIONAL_FIXTURES not configured; "
    "public fixtures are minimal fabricated parser cases",
)


def test_happy_path_lunch_persists_offerings_and_profiles(lunch_command, tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    report = use_case.execute(lunch_command)

    assert report.status == RunStatus.PERSISTED
    assert report.stats["offerings_seen"] == 22
    assert report.stats["inserted_offerings"] == 22
    assert report.stats["labels_fetched"] == 22
    # One label has authoritative nutrition; all remaining source states are
    # retained as accepted page membership without fabricated profiles.
    assert report.stats["profiles_persisted"] == 1
    assert report.stats["profile_linked_offerings"] == 1
    assert report.stats["non_profile_offerings"] == 21
    assert report.stats["source_placeholder"] == 1
    assert report.stats["source_unavailable"] == 20
    assert report.quarantines_by_code == {}
    assert report.advertised_dates == tuple(
        SERVICE_DATE + timedelta(days=offset) for offset in range(7)
    )

    offerings = list(deps.offerings.offerings.values())
    linked = [o for o in offerings if o.profile_id is not None]
    assert len(linked) == 1
    assert linked[0].source_mid == "900000001"


def test_placeholder_label_accepted_without_persisting_authoritative_zeros(
    lunch_command, tmp_path: Path
) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    report = use_case.execute(lunch_command)

    assert report.status == RunStatus.PERSISTED
    assert report.quarantines_by_code == {}

    profiles = deps.profiles.profiles.values()
    halal_food = deps.foods.get_by_name(50, "CYO Halal Bowl")
    assert halal_food is not None
    food_profiles = [p for p in profiles if p.food_id == halal_food]
    assert food_profiles == []  # zeros never entered storage
    page = next(iter(deps.pages.page_versions.values()))
    halal_pin = next(
        pin for pin in deps.pages.memberships[page.page_version_id] if pin.food_id == halal_food
    )
    assert halal_pin.nutrition_source_state is NutritionSourceState.SOURCE_PLACEHOLDER
    assert halal_pin.profile_id is None


def test_empty_breakfast_is_recorded_and_run_succeeds(tmp_path: Path) -> None:
    use_case, _deps = make_use_case(snapshot_root=tmp_path)
    command = IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.BREAKFAST])
    report = use_case.execute(command)

    assert report.status == RunStatus.PERSISTED
    assert report.stats["empty_periods"] == 1
    assert report.stats["inserted_offerings"] == 0
    assert report.quarantines_by_code.get("empty_menu_period") == 1
    assert report.advertised_dates == tuple(
        SERVICE_DATE + timedelta(days=offset) for offset in range(7)
    )


def test_partial_page_replay_retries_without_duplicate_storage(
    lunch_command, tmp_path: Path
) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    first = use_case.execute(lunch_command)
    offerings_after_first = dict(deps.offerings.offerings)
    profiles_after_first = dict(deps.profiles.profiles)

    second = use_case.execute(lunch_command)

    assert first.status == RunStatus.PERSISTED
    # Every ingestion is a distinct immutable accepted observation even when
    # canonical snapshot content is reused.
    assert second.status == RunStatus.PERSISTED
    assert second.stats["pages_skipped_by_hash"] == 0
    assert second.stats["inserted_offerings"] == 0
    assert second.stats["updated_offerings"] == 22
    assert second.stats["profiles_persisted"] == 1
    assert second.quarantines_by_code == {}
    assert deps.offerings.offerings == offerings_after_first
    assert deps.profiles.profiles == profiles_after_first
    assert len(deps.pages.page_versions) == 2


def test_out_of_window_date_rejected_before_fetch(tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)

    class SpySource:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_menu_page(self, *a, **k):  # pragma: no cover
            self.calls += 1
            raise AssertionError("fetch attempted for out-of-window date")

        def fetch_label(self, mid):  # pragma: no cover
            self.calls += 1

    spy = SpySource()
    new_deps = dataclasses.replace(deps, source=spy)
    use_case = IngestStacksUseCase(new_deps)
    command = IngestCommand(service_date=date(2026, 9, 15), meal_periods=[MealPeriod.LUNCH])
    report = use_case.execute(command)

    assert spy.calls == 0
    assert report.status == RunStatus.PARTIAL_FAILURE
    assert report.quarantines_by_code.get("date_out_of_window") == 1


def test_item_limit_rejects_page_before_any_label_fanout(lunch_command, tmp_path: Path) -> None:
    from nutrition_agent.infrastructure.stacks_source.fixture_source import FixtureStacksSource
    from tests.conftest import FIXTURE_DIR

    class CountingSource:
        def __init__(self) -> None:
            self._fixture = FixtureStacksSource(FIXTURE_DIR)
            self.label_calls = 0

        def fetch_menu_page(self, service_date, meal_period):
            return self._fixture.fetch_menu_page(service_date, meal_period)

        def fetch_label(self, mid):  # pragma: no cover - must be blocked by cap
            self.label_calls += 1
            raise AssertionError(mid)

    source = CountingSource()
    use_case, deps = make_use_case(snapshot_root=tmp_path, source=source)
    use_case = IngestStacksUseCase(dataclasses.replace(deps, max_items_per_page=21))

    report = use_case.execute(lunch_command)

    assert report.status == RunStatus.PARTIAL_FAILURE
    assert report.quarantines_by_code == {"item_limit_exceeded": 1}
    assert report.stats["labels_fetched"] == 0
    assert source.label_calls == 0


def test_http_error_body_is_snapshotted_before_status_rejection(
    lunch_command, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    from nutrition_agent.infrastructure.snapshot_store import RawPage

    class StatusSource:
        def fetch_menu_page(self, service_date, meal_period):
            return RawPage(
                source_url="https://source.test/menu",
                method="POST",
                request_params={"meal": meal_period.value},
                body=b"retryable failure body",
                http_status=503,
                fetched_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
            )

        def fetch_label(self, mid):  # pragma: no cover
            raise AssertionError(mid)

    use_case, deps = make_use_case(snapshot_root=tmp_path, source=StatusSource())

    report = use_case.execute(lunch_command)

    assert report.status == RunStatus.FAILED
    assert report.quarantines_by_code == {"transport_failure": 1}
    assert len(deps.snapshot_repo.recorded) == 1
    snapshot, _ = deps.snapshot_repo.recorded[0]
    assert deps.snapshots.load(snapshot) == b"retryable failure body"


def test_partial_failure_isolation_keeps_prior_good_data(lunch_command, tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    use_case.execute(lunch_command)
    snapshot_before = {
        oid: (o.profile_id, o.source_mid) for oid, o in deps.offerings.offerings.items()
    }

    # simulate a later run where the transport dies on the menu page
    class FlakySource:
        def fetch_menu_page(self, service_date, meal_period):
            raise TransportError("connection reset")

        def fetch_label(self, mid):  # pragma: no cover
            raise TransportError("unreachable")

    flaky_deps = dataclasses.replace(deps, source=FlakySource())  # type: ignore[arg-type]
    report = IngestStacksUseCase(flaky_deps).execute(lunch_command)

    assert report.status == RunStatus.FAILED
    snapshot_after = {
        oid: (o.profile_id, o.source_mid) for oid, o in deps.offerings.offerings.items()
    }
    assert snapshot_after == snapshot_before  # prior good data untouched


def test_changed_markup_fail_closed_preserves_rows(lunch_command, tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    use_case.execute(lunch_command)
    before = {oid: o.profile_id for oid, o in deps.offerings.offerings.items()}
    parser_version_before = {
        pid: p.provenance.parser_version for pid, p in deps.profiles.profiles.items()
    }
    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    pages_before = dict(pages.page_versions)

    broken_dir = tmp_path / "broken-fixtures"
    broken_dir.mkdir()
    for f in Path(use_case._d.source._dir).glob("*.html"):  # noqa: SLF001
        content = f.read_text(encoding="utf-8")
        if "daily_menu_lunch" in f.name:
            content = content.replace("daily-menu-item__link", "item-link-broken")
        (broken_dir / f.name).write_text(content)

    from nutrition_agent.infrastructure.stacks_source.fixture_source import FixtureStacksSource

    broken_deps = dataclasses.replace(deps, source=FixtureStacksSource(broken_dir))
    report = IngestStacksUseCase(broken_deps).execute(lunch_command)

    assert report.status == RunStatus.PARTIAL_FAILURE
    # one quarantine record per unparsable item + page-level quarantine flag
    assert report.quarantines_by_code.get("parser_markup_mismatch") == 22
    assert report.stats["pages_quarantined"] == 1
    assert report.stats["inserted_offerings"] == 0
    after = {oid: o.profile_id for oid, o in deps.offerings.offerings.items()}
    assert after == before
    parser_version_after = {
        pid: p.provenance.parser_version for pid, p in deps.profiles.profiles.items()
    }
    assert parser_version_after == parser_version_before
    # The previously accepted page remains authority; the broken retry creates
    # no additional accepted observation.
    assert pages.page_versions == pages_before


def test_snapshot_replay_determinism(lunch_command, tmp_path: Path) -> None:
    use_case, deps = make_use_case(snapshot_root=tmp_path)
    use_case.execute(lunch_command)

    # Replay: reload the stored menu snapshot bytes and re-run the parser;
    # the normalized offering set must be byte-identical to what was persisted.
    from nutrition_agent.infrastructure.stacks_source.menu_parser import MenuPageParser
    from nutrition_agent.infrastructure.stacks_source.normalizer import (
        assign_occurrence_ordinals,
        menu_day_from_parsed,
    )

    menu_records = [
        (ref, version)
        for ref, version in deps.snapshot_repo.recorded
        if ref.request_params.get("meal") == "Lunch"
    ]
    assert menu_records, "menu snapshot must be recorded"
    ref, version = menu_records[0]
    assert version == "2026-08-27.m8.1"

    body = deps.snapshots.load(ref).decode("utf-8")
    reparsed = MenuPageParser().parse(body, SERVICE_DATE, MealPeriod.LUNCH, 50)
    assert reparsed.ok and reparsed.value
    day = menu_day_from_parsed(reparsed.value)
    ordinals = assign_occurrence_ordinals(day.offerings)

    stored_offerings = {
        (o.category_position, o.item_position, o.source_mid): o.profile_id is not None
        for o in deps.offerings.offerings.values()
    }
    assert len(stored_offerings) == 22

    replay_keys = {(i.category_position, i.item_position, i.source_mid) for i in day.offerings}
    assert replay_keys == set(stored_offerings.keys())

    turkey_ordinals = [
        ordinals[idx]
        for idx, offering in enumerate(day.offerings)
        if offering.name_normalized == "Turkey Burger"
    ]
    assert sorted(turkey_ordinals) == [0, 1]
