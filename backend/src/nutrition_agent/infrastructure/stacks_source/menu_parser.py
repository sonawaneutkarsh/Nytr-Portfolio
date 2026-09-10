"""Parser for the server-rendered Stacks daily menu page.

Parse contract (docs/STACKS_DISCOVERY.md section 4): text content and field
order are authoritative; wrapper class names are secondary hints.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Final

from bs4 import BeautifulSoup, Tag

from nutrition_agent.domain.stacks.entities import (
    DIETARY_TAG_BY_ALT,
    DietaryTag,
    MealPeriod,
)
from nutrition_agent.domain.stacks.parsing import (
    ParsedCategory,
    ParsedMenuItem,
    ParsedMenuPage,
    ParseFailure,
    ParseResult,
)

_MID_RE: Final = re.compile(r"^nutrition-label\.cfm\?mid=(\d+)$")
_DATE_OPT_RE: Final = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$")


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _parse_mdyy(value: str) -> date | None:
    match = _DATE_OPT_RE.match(value.strip())
    if match is None:
        return None
    month, day, short_year = (int(part) for part in match.groups())
    return date(2000 + short_year, month, day)


def _selected_value(soup: BeautifulSoup, select_id: str) -> str | None:
    matches = soup.select(f"select#{select_id} option[selected]")
    if matches:
        value = matches[0].get("value")
        return str(value) if value is not None else None
    return None


def _extract_tags(item: Tag) -> tuple[tuple[DietaryTag, ...], list[str]]:
    tags: list[DietaryTag] = []
    unknown: list[str] = []
    icons = item.find("span", class_="daily-menu-item__icons")
    container = icons if isinstance(icons, Tag) else item
    for img in container.find_all("img"):
        src = img.get("src") or ""
        alt = img.get("alt") or ""
        if "LegendImages" in str(src):
            tag = DIETARY_TAG_BY_ALT.get(_normalize_text(str(alt)))
            if tag is None:
                unknown.append(str(alt))
            else:
                tags.append(tag)
    return tuple(tags), unknown


class MenuPageParser:
    def parse(
        self,
        html: str,
        requested_date: date,
        requested_meal: MealPeriod,
        campus_id: int,
    ) -> ParseResult[ParsedMenuPage]:
        soup = BeautifulSoup(html, "lxml")

        echo_date = _selected_value(soup, "selMenuDate")
        echo_meal = _selected_value(soup, "selMeal")
        echo_campus = _selected_value(soup, "selCampus")
        selection_echo_ok = (
            echo_date is not None
            and _parse_mdyy(echo_date) == requested_date
            and echo_meal == requested_meal.value
            and echo_campus == str(campus_id)
        )

        window_values = [
            option.get("value")
            for option in soup.select("select#selMenuDate option")
            if isinstance(option, Tag) and option.get("value")
        ]
        parsed_window = [d for d in (_parse_mdyy(str(v)) for v in window_values) if d is not None]
        if len(parsed_window) != len(window_values):
            return ParseResult.err(
                code="PARSER_MARKUP_MISMATCH",
                detail="unparseable value in selMenuDate options",
                selector_path="select#selMenuDate option@value",
            )
        if not parsed_window and not self._is_empty_period(soup):
            return ParseResult.err(
                code="PARSER_MARKUP_MISMATCH",
                detail="date dropdown missing",
                selector_path="select#selMenuDate",
            )

        categories: list[ParsedCategory] = []
        item_failures: list[ParseFailure] = []

        details_blocks = soup.find_all("details")
        for details in details_blocks:
            summary = details.find("summary")
            title_elements = (
                [
                    element
                    for selector in ("category-name", "nutrition-category-title")
                    if isinstance(
                        element := summary.find(class_=selector),
                        Tag,
                    )
                ]
                if isinstance(summary, Tag)
                else []
            )
            category_names = {_normalize_text(element.get_text()) for element in title_elements}
            if len(category_names) != 1:
                return ParseResult.err(
                    code="PARSER_MARKUP_MISMATCH",
                    detail="details block without one unambiguous supported category title",
                    selector_path=(
                        "details > summary > (.category-name | .nutrition-category-title)"
                    ),
                )
            category_position = len(categories) + 1
            category_name = category_names.pop()

            items: list[ParsedMenuItem] = []
            items_container = details.find("div", class_="category-items")
            if not isinstance(items_container, Tag):
                return ParseResult.err(
                    code="PARSER_MARKUP_MISMATCH",
                    detail="category without category-items container",
                    selector_path="details > div.category-items",
                )
            for item in items_container.find_all("div", class_="daily-menu-item"):
                item_position = len(items) + 1
                anchor = item.find("a", class_="daily-menu-item__link")
                if not isinstance(anchor, Tag):
                    item_failures.append(
                        ParseFailure(
                            code="PARSER_MARKUP_MISMATCH",
                            detail=(
                                "item without label link at "
                                f"category={category_name} pos={item_position}"
                            ),
                            selector_path="div.daily-menu-item > a.daily-menu-item__link",
                        )
                    )
                    continue
                href = str(anchor.get("href") or "")
                mid_match = _MID_RE.match(href)
                if mid_match is None:
                    item_failures.append(
                        ParseFailure(
                            code="PARSER_MARKUP_MISMATCH",
                            detail=f"unexpected item href '{href}'",
                            selector_path="a.daily-menu-item__link@href",
                        )
                    )
                    continue

                attr_name = item.get("data-menu-item-name")
                aria_label = anchor.get("aria-label")
                text_name = anchor.get_text()
                names = {
                    _normalize_text(str(attr_name)) if attr_name else "",
                    _normalize_text(str(aria_label)) if aria_label else "",
                    _normalize_text(text_name),
                }
                names.discard("")
                if len(names) != 1:
                    item_failures.append(
                        ParseFailure(
                            code="PARSER_MARKUP_MISMATCH",
                            detail=(
                                f"item name sources disagree at mid={mid_match.group(1)}: "
                                f"{sorted(names)}"
                            ),
                            selector_path="div.daily-menu-item[data-menu-item-name]",
                        )
                    )
                    continue

                tags, unknown_alts = _extract_tags(item)
                if unknown_alts:
                    item_failures.append(
                        ParseFailure(
                            code="PARSER_MARKUP_MISMATCH",
                            detail=(
                                f"unknown dietary legend alt text {unknown_alts} "
                                f"at mid={mid_match.group(1)}"
                            ),
                            selector_path="img[src*='LegendImages']@alt",
                        )
                    )
                    continue

                items.append(
                    ParsedMenuItem(
                        name_raw=_normalize_text(text_name),
                        mid_instance=mid_match.group(1),
                        dietary_tags=tags,
                        category_position=category_position,
                        item_position=item_position,
                    )
                )
            categories.append(
                ParsedCategory(name=category_name, position=category_position, items=tuple(items))
            )

        empty_period = self._is_empty_period(soup)
        if not categories and not empty_period:
            return ParseResult.err(
                code="PARSER_MARKUP_MISMATCH",
                detail="no categories and no empty-state marker",
                selector_path="body",
            )

        return ParseResult.ok_result(
            ParsedMenuPage(
                requested_date=requested_date,
                requested_meal=requested_meal,
                campus_id=campus_id,
                selection_echo_ok=selection_echo_ok,
                date_window=tuple(sorted(parsed_window)),
                categories=tuple(categories),
                empty_period=empty_period,
                item_failures=tuple(item_failures),
            )
        )

    @staticmethod
    def _is_empty_period(soup: BeautifulSoup) -> bool:
        if soup.find_all("details"):
            return False
        return EMPTY_SENTINEL_RE.search(soup.get_text()) is not None


EMPTY_SENTINEL_RE: Final = re.compile(r"No items found")
