"""Menu view tests: availability vs catalog, three period states."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nutrition_agent.domain.planning.menu_view import MenuDayView, PeriodMenu
from nutrition_agent.domain.stacks.entities import MealPeriod
from tests.unit.planning_helpers import make_offering

FETCHED_AT = datetime(2026, 8, 24, 6, 0, tzinfo=UTC)


def test_explicitly_empty_period_cannot_carry_offerings() -> None:
    with pytest.raises(ValueError, match="explicitly_empty"):
        PeriodMenu(offerings=(make_offering(1).build(),), explicitly_empty=True)  # type: ignore[arg-type]


def test_period_states_are_distinguishable() -> None:
    lunch = PeriodMenu(offerings=(make_offering(1).build(),))  # type: ignore[list-item]
    breakfast = PeriodMenu(offerings=(), explicitly_empty=True)
    view = MenuDayView(
        service_date=datetime(2026, 8, 24).date(),
        periods={MealPeriod.LUNCH: lunch, MealPeriod.BREAKFAST: breakfast},
        fetched_at=FETCHED_AT,
        snapshot_sha256="menu-sha",
    )
    # present with offerings
    assert view.period(MealPeriod.LUNCH) is lunch
    assert (
        view.period(MealPeriod.LUNCH) is not None
        and len(
            view.period(MealPeriod.LUNCH).offerings  # type: ignore[union-attr]
        )
        == 1
    )
    # present and VALIDATED empty
    breakfast_menu = view.period(MealPeriod.BREAKFAST)
    assert breakfast_menu is not None and breakfast_menu.explicitly_empty is True
    # ABSENT key: data gap, not source truth
    assert view.period(MealPeriod.DINNER) is None


def test_catalog_food_absent_from_offerings_is_not_in_view() -> None:
    # The catalog (stacks_food) may know 500 foods; only the day's menu_offering
    # rows are visible here. A catalog-only food simply never appears.
    view = MenuDayView(
        service_date=datetime(2026, 8, 24).date(),
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(make_offering(1).build(),))},  # type: ignore[list-item]
        fetched_at=FETCHED_AT,
        snapshot_sha256="menu-sha",
    )
    lunch = view.period(MealPeriod.LUNCH)
    assert lunch is not None
    names = {offering.name_normalized for offering in lunch.offerings}
    assert names == {"Food 1"}
    assert "Food 999" not in names  # catalog-only item is unavailable by construction


def test_duplicate_name_occurrences_preserved() -> None:
    first = make_offering(1, name="Turkey Burger").build()
    second = make_offering(2, name="Turkey Burger").build()
    lunch = PeriodMenu(offerings=(first, second))  # type: ignore[list-item]
    assert len(lunch.offerings) == 2
    assert lunch.offerings[0].offering_id != lunch.offerings[1].offering_id


def test_offering_without_profile_carries_none() -> None:
    offering = make_offering(7, with_profile=False).build()
    assert offering.profile is None  # type: ignore[attr-defined]
    assert offering.profile_sha256 is None  # type: ignore[attr-defined]


def test_menu_view_frozen_mapping() -> None:
    view = MenuDayView(
        service_date=datetime(2026, 8, 24).date(),
        periods={},
        fetched_at=FETCHED_AT,
        snapshot_sha256="sha",
    )
    with pytest.raises(TypeError):
        view.periods[MealPeriod.LUNCH] = PeriodMenu(offerings=())  # type: ignore[index]
