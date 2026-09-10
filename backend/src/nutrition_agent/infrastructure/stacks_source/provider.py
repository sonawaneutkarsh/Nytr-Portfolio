"""Stacks Source A request builder: turns domain requests into RawPages."""

from __future__ import annotations

from datetime import date

from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.infrastructure.http_transport import (
    FetchResult,
    HttpRequest,
    HttpTransport,
)
from nutrition_agent.infrastructure.snapshot_store import RawPage
from nutrition_agent.infrastructure.stacks_source.constants import (
    LABEL_PAGE_PATH,
    MENU_PAGE_PATH,
    STACKS_CAMPUS_ID,
)

BASE_URL = "https://institutional-menu.example.invalid"


def format_menu_date(service_date: date) -> str:
    return f"{service_date.month}/{service_date.day}/{service_date.year % 100:02d}"


class StacksHttpSource:
    def __init__(self, transport: HttpTransport, *, base_url: str = BASE_URL) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")

    @property
    def transport(self) -> HttpTransport:
        return self._transport

    def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage:
        params = {
            "selMenuDate": format_menu_date(service_date),
            "selMeal": meal_period.value,
            "selCampus": str(STACKS_CAMPUS_ID),
        }
        url = f"{self._base_url}{MENU_PAGE_PATH}"
        result = self._fetch(url, "POST", params)
        return self._to_raw_page(url, "POST", params, result, "text/html")

    def fetch_label(self, mid_instance: str) -> RawPage:
        url = f"{self._base_url}{LABEL_PAGE_PATH}?mid={mid_instance}"
        result = self._fetch(url, "GET", {})
        return self._to_raw_page(url, "GET", {"mid": mid_instance}, result, "text/html")

    def _fetch(self, url: str, method: str, form_fields: dict[str, str]) -> FetchResult:
        return self._transport.fetch(HttpRequest(url=url, method=method, form_fields=form_fields))

    @staticmethod
    def _to_raw_page(
        url: str,
        method: str,
        params: dict[str, str],
        result: FetchResult,
        content_type: str,
    ) -> RawPage:
        return RawPage(
            source_url=url,
            method=method,
            request_params=params,
            body=result.body,
            http_status=result.status,
            fetched_at=result.fetched_at,
            content_type=content_type,
        )
