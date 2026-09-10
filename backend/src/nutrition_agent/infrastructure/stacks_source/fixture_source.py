"""Fixture-backed StacksSource for offline demos and tests. ZERO network access.

Maps known fixture files to (date, meal) and mid requests. Unknown mids return
the real site's sentinel body so downstream behavior matches production.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.infrastructure.snapshot_store import RawPage

_MENU_FILES: dict[tuple[date, MealPeriod], str] = {
    (date(2026, 8, 21), MealPeriod.LUNCH): "daily_menu_lunch_2026-08-21.html",
    (date(2026, 8, 21), MealPeriod.BREAKFAST): "daily_menu_breakfast_empty_2026-08-21.html",
    (date(2026, 8, 27), MealPeriod.LUNCH): "daily_menu_lunch_2026-08-27_current.html",
}

_LABEL_FILES: dict[str, str] = {
    "900000001": "nutrition_label_standard_roast_chicken.html",
    "900000002": "nutrition_label_placeholder_halal_bowl.html",
    "910000001": "nutrition_label_current_card_2026-08-27.html",
}

_SENTINEL_BODY: str = (
    "<html><body>Nutrition information is not available for the selected item.</body></html>"
)

_FIXTURE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class FixtureStacksSource:
    def __init__(self, fixture_dir: Path, fixed_now: str = "2026-08-21T12:00:00+00:00") -> None:
        self._dir = fixture_dir
        self._fixed_now = fixed_now

    def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
        filename = _MENU_FILES.get((service_date, meal_period))
        if filename is None:
            raise FileNotFoundError(
                f"no fixture menu for {service_date} {meal_period.value}; "
                "fixture source covers only explicitly captured pages"
            )
        return self._load(filename, f"fixture://menus/{filename}", {"meal": meal_period.value})

    def fetch_label(self, mid_instance: str) -> RawPage:
        filename = _LABEL_FILES.get(mid_instance)
        if filename is None:
            return RawPage(
                source_url=f"fixture://labels/{mid_instance}",
                method="GET",
                request_params={"mid": mid_instance},
                body=_SENTINEL_BODY.encode("utf-8"),
                http_status=200,
                fetched_at=self._now(),
            )
        return self._load(filename, f"fixture://labels/{filename}", {"mid": mid_instance})

    def _load(self, filename: str, url: str, params: dict[str, str]) -> RawPage:
        body = (self._dir / filename).read_bytes()
        return RawPage(
            source_url=url,
            method="POST" if "menu" in filename else "GET",
            request_params=params,
            body=body,
            http_status=200,
            fetched_at=self._now(),
        )

    def _now(self) -> datetime:
        return datetime.fromisoformat(self._fixed_now)
