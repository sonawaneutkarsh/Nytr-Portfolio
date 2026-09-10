"""Synthetic offline helpers for parser and SQL tests.

No institutional page content is distributed in the public mirror.
"""

from __future__ import annotations

import csv
import html
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from nutrition_agent.domain.stacks.entities import (
    DietaryTag,
    MealPeriod,
    NutritionSourceState,
)
from nutrition_agent.infrastructure.snapshot_store import RawPage

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "stacks"
SERVICE_DATE = date(2026, 8, 27)
FETCHED_AT = datetime(2026, 8, 27, 16, 0, tzinfo=UTC)


@dataclass(frozen=True)
class RetainedMenuItem:
    name: str
    source_mid: str
    category_name: str
    category_position: int
    item_position: int
    occurrence_ordinal: int
    dietary_tags: tuple[DietaryTag, ...]
    nutrition_snapshot_sha256: str
    nutrition_source_state: NutritionSourceState


def retained_menu_items() -> tuple[RetainedMenuItem, ...]:
    path = FIXTURE_ROOT / "retained_lunch_2026-08-27_memberships.tsv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle, delimiter="\t")
        return tuple(
            RetainedMenuItem(
                name=row["name_normalized"],
                source_mid=row["source_mid"],
                category_name=row["category_name"],
                category_position=int(row["category_position"]),
                item_position=int(row["item_position"]),
                occurrence_ordinal=int(row["occurrence_ordinal"]),
                dietary_tags=tuple(
                    DietaryTag(value) for value in row["dietary_tags"].split(",") if value
                ),
                nutrition_snapshot_sha256=row["nutrition_snapshot_sha256"],
                nutrition_source_state=NutritionSourceState(row["nutrition_source_state"]),
            )
            for row in rows
        )


RETAINED_LABEL_FIXTURES = {
    "990000095": "retained_2026-08-27_cyo_burger.html",
    "990000096": "retained_2026-08-27_cyo_chicken_sandwich.html",
    "990000097": "retained_2026-08-27_cyo_halal_bowl.html",
    "990000098": "retained_demo_incomplete_b.html",
    "990000099": "retained_2026-08-27_cyo_chicken_sandwich.html",
}


def _menu_html(items: tuple[RetainedMenuItem, ...]) -> bytes:
    categories: dict[tuple[int, str], list[RetainedMenuItem]] = {}
    for item in items:
        categories.setdefault((item.category_position, item.category_name), []).append(item)
    sections: list[str] = []
    for (position, category), members in categories.items():
        rows: list[str] = []
        for item in members:
            source_alt = {
                DietaryTag.GLUTEN_FRIENDLY: ("Gluten Friendly - made w/o gluten-containing items")
            }
            icons = "".join(
                f'<img alt="{html.escape(source_alt.get(tag, tag.value))}" '
                f'aria-label="{html.escape(source_alt.get(tag, tag.value))}" '
                'src="/MENUS/LegendImages/fixture.gif">'
                for tag in item.dietary_tags
            )
            rows.append(
                '<div class="menu-items daily-menu-item" '
                f'data-menu-item-name="{html.escape(item.name)}">'
                f'<a class="daily-menu-item__link" href="nutrition-label.cfm?mid='
                f'{item.source_mid}">{html.escape(item.name)}</a>'
                f'<span class="daily-menu-item__icons">{icons}</span></div>'
            )
        sections.append(
            f'<details class="menu-category-section" id="dailyMenuCategory{position}">'
            '<summary class="menu-category-summary">'
            f'<span class="nutrition-category-title">{html.escape(category)}</span></summary>'
            f'<div class="category-items">{"".join(rows)}</div></details>'
        )
    return (
        '<select id="selMenuDate"><option selected value="8/27/26">'
        "Thursday, August 27</option></select>"
        '<select id="selMeal"><option selected value="Lunch">Lunch</option></select>'
        '<select id="selCampus"><option selected value="50">'
        "Demo dining</option></select>" + "".join(sections)
    ).encode()


class RetainedLiveFixtureSource:
    """Offline-only source reconstructed from retained public source facts."""

    def __init__(self) -> None:
        self.items = retained_menu_items()

    def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
        assert service_date == SERVICE_DATE
        assert meal_period is MealPeriod.LUNCH
        return RawPage(
            source_url="fixture://retained-live/menu",
            method="POST",
            request_params={"selMenuDate": "8/27/26", "selMeal": "Lunch", "selCampus": "50"},
            body=_menu_html(self.items),
            http_status=200,
            fetched_at=FETCHED_AT,
        )

    def fetch_label(self, mid_instance: str) -> RawPage:
        filename = RETAINED_LABEL_FIXTURES.get(
            mid_instance,
            "nutrition_label_standard_roast_chicken.html",
        )
        return RawPage(
            source_url=f"fixture://retained-live/label/{mid_instance}",
            method="GET",
            request_params={"mid": mid_instance},
            body=(FIXTURE_ROOT / filename).read_bytes(),
            http_status=200,
            fetched_at=FETCHED_AT,
        )


__all__ = [
    "FETCHED_AT",
    "RETAINED_LABEL_FIXTURES",
    "RetainedLiveFixtureSource",
    "RetainedMenuItem",
    "SERVICE_DATE",
    "retained_menu_items",
]
