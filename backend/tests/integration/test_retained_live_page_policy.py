"""Offline acceptance proof reconstructed from the retained M8 live page."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

from nutrition_agent.application.ingest_stacks import IngestStacksUseCase
from nutrition_agent.application.ports import IngestCommand
from nutrition_agent.db.in_memory_repos import InMemoryMenuPageVersionRepository
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.eligibility import ReasonCode, offering_gate
from nutrition_agent.domain.planning.menu_view import OfferingView
from nutrition_agent.domain.planning.policy import SlotPolicy
from nutrition_agent.domain.stacks.entities import MealPeriod, NutritionSourceState
from nutrition_agent.domain.stacks.ingestion import RunStatus
from tests.conftest import FixedClock, make_use_case
from tests.retained_live_fixtures import (
    FETCHED_AT,
    SERVICE_DATE,
    RetainedLiveFixtureSource,
)


def test_retained_99_occurrence_page_is_complete_atomic_and_planner_safe(
    tmp_path: Path,
) -> None:
    source = RetainedLiveFixtureSource()
    use_case, deps = make_use_case(snapshot_root=tmp_path, source=source)
    clock = FixedClock()
    clock.value = FETCHED_AT
    deps = replace(deps, clock=clock)
    use_case = IngestStacksUseCase(deps)

    report = use_case.execute(
        IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH])
    )

    assert report.status is RunStatus.PERSISTED
    assert report.quarantines_by_code == {}
    assert report.stats["offerings_seen"] == 99
    assert report.stats["profile_linked_offerings"] == 94
    assert report.stats["non_profile_offerings"] == 5
    assert report.stats["source_placeholder"] == 3
    assert report.stats["source_incomplete"] == 2
    assert report.stats["source_unavailable"] == 0
    assert report.stats["profiles_persisted"] == 94
    # Occurrence counts and canonical profile-row counts are intentionally
    # distinct. Three repeated foods share a response, while the two retained
    # Turkey Burger occurrences have different exact nutrition responses.
    assert len(deps.profiles.profiles) == 91

    pages = deps.pages
    assert isinstance(pages, InMemoryMenuPageVersionRepository)
    assert len(pages.page_versions) == 1
    page = next(iter(pages.page_versions.values()))
    pins = pages.memberships[page.page_version_id]
    assert page.offering_count == len(pins) == 99
    assert len({pin.offering_id for pin in pins}) == 99
    assert all(pin.nutrition_snapshot_id is not None for pin in pins)
    states = Counter(pin.nutrition_source_state for pin in pins)
    assert states == {
        NutritionSourceState.PROFILE_AVAILABLE: 94,
        NutritionSourceState.SOURCE_PLACEHOLDER: 3,
        NutritionSourceState.SOURCE_INCOMPLETE: 2,
    }

    names = {pin.name_normalized: pin for pin in pins}
    assert names["Demo Placeholder A"].nutrition_source_state is (
        NutritionSourceState.SOURCE_PLACEHOLDER
    )
    assert names["Demo Placeholder B"].nutrition_source_state is (
        NutritionSourceState.SOURCE_PLACEHOLDER
    )
    assert names["Demo Placeholder C"].nutrition_source_state is (
        NutritionSourceState.SOURCE_PLACEHOLDER
    )
    assert names["Demo Incomplete A"].nutrition_source_state is (
        NutritionSourceState.SOURCE_INCOMPLETE
    )
    assert names["Demo Incomplete B"].nutrition_source_state is (
        NutritionSourceState.SOURCE_INCOMPLETE
    )
    for name in (
        "Demo Placeholder A",
        "Demo Placeholder B",
        "Demo Placeholder C",
        "Demo Incomplete A",
        "Demo Incomplete B",
    ):
        assert names[name].profile_id is None

    snapshot_refs = {ref.snapshot_id: ref for ref, _ in deps.snapshot_repo.recorded}
    policy = SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
    eligible_profiled = 0
    for pin in pins:
        profile = deps.profiles.profiles.get(pin.profile_id) if pin.profile_id else None
        nutrition_ref = snapshot_refs[pin.nutrition_snapshot_id]
        view = OfferingView(
            offering_id=pin.offering_id,
            food_id=pin.food_id,
            name_normalized=pin.name_normalized,
            source_mid=pin.source_mid,
            occurrence_ordinal=pin.occurrence_ordinal,
            category_name=pin.category_name,
            dietary_tags=pin.dietary_tags,
            profile=profile,
            profile_sha256=profile.provenance.content_sha256 if profile else None,
            snapshot_sha256="retained-menu",
            nutrition_source_state=pin.nutrition_source_state,
            nutrition_snapshot_sha256=nutrition_ref.content_sha256,
        )
        rejection = offering_gate(view, policy)
        if pin.profile_id is None:
            assert rejection is not None
            assert rejection.reason is ReasonCode.NO_PROFILE_LINKED
        elif rejection is None:
            eligible_profiled += 1
    assert eligible_profiled > 0
