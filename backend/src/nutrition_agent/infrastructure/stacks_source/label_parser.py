"""Parser for Stacks nutrition label pages (Source A) with FoodPro fallback.

Parse contract (docs/STACKS_DISCOVERY.md section 6): text content and field
order are authoritative. Structural selectors are hints; a text-scanning
fallback keeps the parser working across the two known page generations.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Final

from bs4 import BeautifulSoup, Tag

from nutrition_agent.domain.stacks.entities import NUTRIENT_KEY_BY_LABEL, NutritionSourceState
from nutrition_agent.domain.stacks.parsing import (
    ParsedLabel,
    ParsedNutrientRow,
    ParseResult,
)
from nutrition_agent.infrastructure.stacks_source.constants import (
    INVALID_MID_SENTINEL,
    PLACEHOLDER_INGREDIENTS_TEXT,
)

_AMOUNT_RE: Final = re.compile(r"^(\d+(?:\.\d+)?)(g|mg|mcg)$")
_DV_ONLY_RE: Final = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")
_DASH_RE: Final = re.compile(r"^[-\u2013\u2014&]*[-\u2013\u2014]+[-\u2013\u2014&]*$")

_NUTRIENT_ALIASES: Final[dict[str, str]] = {
    "Tot. Carb.": "Total Carbohydrate",
    "Sat. Fat": "Saturated Fat",
    "Vitamin D - mcg": "Vitamin D",
}


def _clean_cell(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _canonical_label(raw: str) -> str | None:
    label = _NUTRIENT_ALIASES.get(raw, raw)
    return NUTRIENT_KEY_BY_LABEL.get(label)


def _parse_dv(text: str) -> Decimal | None:
    stripped = text.strip()
    if not stripped or _DASH_RE.match(stripped):
        return None
    if stripped.endswith("%"):
        try:
            return Decimal(stripped[:-1].strip())
        except InvalidOperation:
            return None
    return None


class LabelPageParser:
    def parse(self, html: str) -> ParseResult[ParsedLabel]:
        soup = BeautifulSoup(html, "lxml")
        page_text = soup.get_text()

        if INVALID_MID_SENTINEL in page_text:
            return ParseResult.ok_result(
                ParsedLabel(
                    item_name=None,
                    serving_basis_raw=None,
                    calories_text=None,
                    rows=(),
                    ingredients_raw=None,
                    allergens=(),
                    invalid_mid_sentinel=True,
                    placeholder=False,
                    source_state=NutritionSourceState.SOURCE_UNAVAILABLE,
                )
            )

        name_el = (
            soup.find(class_="nutrition-item-name")
            or soup.find(class_="recipe-title")
            or soup.find(class_="labelheader")
            or soup.find("h1")
        )
        item_name = _clean_cell(name_el.get_text()) if isinstance(name_el, Tag) else None

        serving_basis = self._extract_serving_basis(soup)
        calories_text = self._extract_calories(soup)

        for row in soup.select(".nutrition-facts-card .fact-row"):
            if not all(
                isinstance(row.find(class_=class_name), Tag)
                for class_name in ("fact-name", "fact-amount", "fact-dv")
            ):
                return ParseResult.err(
                    code="NUTRITION_MALFORMED",
                    detail="card nutrient row is missing name, amount, or daily value",
                    selector_path=".nutrition-facts-card .fact-row",
                )

        rows: list[ParsedNutrientRow] = []
        for cells in self._iter_row_cells(soup):
            batch_rows, malformed = self._rows_from_cells(cells)
            if malformed is not None:
                return ParseResult.err(
                    code="NUTRITION_MALFORMED",
                    detail=malformed,
                    selector_path="table tr td",
                )
            rows.extend(batch_rows)

        if self._looks_like_nutrition_label(soup) and not rows:
            return ParseResult.err(
                code="NUTRITION_MALFORMED",
                detail="nutrition label contained no recognized nutrient rows",
                selector_path="table tr | .nutrition-facts-card .fact-row",
            )

        recognized_all_empty = self._is_recognized_all_empty_card(
            soup,
            tuple(rows),
            calories_text,
        )
        if (
            soup.select_one(".nutrition-facts-card") is not None
            and calories_text is None
            and not any(row.amount_text is not None or row.dv_percent is not None for row in rows)
            and not recognized_all_empty
        ):
            return ParseResult.err(
                code="NUTRITION_MALFORMED",
                detail="nutrition label contained no authoritative nutrient values",
                selector_path=".nutrition-facts-card .fact-row",
            )

        ingredients_raw = self._extract_section_text(soup, "Ingredients")
        allergens_raw = self._extract_section_text(soup, "Allergens")
        allergens = tuple(part.strip() for part in (allergens_raw or "").split(",") if part.strip())

        placeholder = self._detect_placeholder(ingredients_raw, tuple(rows), calories_text)
        source_state = (
            NutritionSourceState.SOURCE_PLACEHOLDER
            if placeholder
            else (
                NutritionSourceState.SOURCE_INCOMPLETE
                if recognized_all_empty
                else NutritionSourceState.PROFILE_AVAILABLE
            )
        )

        return ParseResult.ok_result(
            ParsedLabel(
                item_name=item_name,
                serving_basis_raw=serving_basis,
                calories_text=calories_text,
                rows=tuple(rows),
                ingredients_raw=ingredients_raw,
                allergens=allergens,
                invalid_mid_sentinel=False,
                placeholder=placeholder,
                source_state=source_state,
            )
        )

    @staticmethod
    def _is_recognized_all_empty_card(
        soup: BeautifulSoup,
        rows: tuple[ParsedNutrientRow, ...],
        calories_text: str | None,
    ) -> bool:
        """Recognize only the complete current-card schema with no values.

        A short, malformed, unknown, or ambiguously populated card remains a
        parser failure. This classification is source data, not a profile.
        """
        if soup.select_one(".nutrition-facts-card") is None or calories_text is not None:
            return False
        expected_keys = set(NUTRIENT_KEY_BY_LABEL.values())
        row_keys = [row.key for row in rows]
        return (
            len(rows) == len(expected_keys)
            and None not in row_keys
            and set(row_keys) == expected_keys
            and all(row.amount_text is None and row.dv_percent is None for row in rows)
        )

    @staticmethod
    def _extract_serving_basis(soup: BeautifulSoup) -> str | None:
        label = soup.find(class_="serving-label")
        value = soup.find(class_="serving-value")
        if isinstance(label, Tag) and isinstance(value, Tag):
            return _clean_cell(value.get_text())
        for summary in soup.select(".nutrition-summary .summary-value"):
            strong = summary.find("strong")
            if isinstance(strong, Tag) and _clean_cell(strong.get_text()).rstrip(":") == (
                "Serving Size"
            ):
                text = _clean_cell(summary.get_text(" ", strip=True))
                return text.removeprefix("Serving Size").strip() or None
        match = re.search(r"Serving Size\s+([^\n|]+)", soup.get_text())
        if match:
            candidate = _clean_cell(match.group(1))
            for terminator in ("Calories", "*Percent"):
                idx = candidate.find(terminator)
                if idx > 0:
                    candidate = _clean_cell(candidate[:idx])
            return candidate or None
        return None

    @staticmethod
    def _extract_calories(soup: BeautifulSoup) -> str | None:
        value = soup.find(class_="calories-value")
        if isinstance(value, Tag):
            return _clean_cell(value.get_text())
        for summary in soup.select(".nutrition-summary .summary-value"):
            strong = summary.find("strong")
            if isinstance(strong, Tag) and _clean_cell(strong.get_text()).rstrip(":") == "Calories":
                text = _clean_cell(summary.get_text(" ", strip=True))
                return text.removeprefix("Calories:").strip() or None
        match = re.search(r"Calories:?\s*(\d[\d,]*)", soup.get_text())
        return match.group(1) if match else None

    @staticmethod
    def _iter_row_cells(soup: BeautifulSoup) -> list[list[str]]:
        tables = soup.find_all("table")
        cell_rows: list[list[str]] = []
        for table in tables:
            for tr in table.find_all("tr"):
                cells = [_clean_cell(td.get_text()) for td in tr.find_all(["td", "th"])]
                cells = [c for c in cells if c]
                if cells:
                    cell_rows.append(cells)
        for row in soup.select(".nutrition-facts-card .fact-row"):
            name = row.find(class_="fact-name")
            amount = row.find(class_="fact-amount")
            dv = row.find(class_="fact-dv")
            assert isinstance(name, Tag)
            assert isinstance(amount, Tag)
            assert isinstance(dv, Tag)
            cell_rows.append(
                [
                    _clean_cell(name.get_text()),
                    _clean_cell(amount.get_text()),
                    _clean_cell(dv.get_text()),
                ]
            )
        return cell_rows

    @staticmethod
    def _looks_like_nutrition_label(soup: BeautifulSoup) -> bool:
        return bool(
            soup.find(class_="nutrition-facts-card")
            or soup.find(class_="nutrition-facts")
            or soup.find(class_="nutrition-summary")
            or soup.find(class_="recipe-title")
            or soup.find(string=re.compile(r"^Nutrition Facts$"))
        )

    def _rows_from_cells(self, cells: list[str]) -> tuple[list[ParsedNutrientRow], str | None]:
        rows: list[ParsedNutrientRow] = []
        index = 0
        while index < len(cells):
            canonical = _canonical_label(cells[index])
            if canonical is None:
                index += 1
                continue
            amount_text: str | None = None
            dv_percent: Decimal | None = None
            consumed = 1
            if index + 1 < len(cells):
                candidate = cells[index + 1]
                if _AMOUNT_RE.match(candidate):
                    amount_text = candidate
                    consumed = 2
                    if index + 2 < len(cells) and (
                        _DV_ONLY_RE.match(cells[index + 2]) or _DASH_RE.match(cells[index + 2])
                    ):
                        dv_percent = _parse_dv(cells[index + 2])
                        consumed = 3
                elif _DV_ONLY_RE.match(candidate):
                    dv_percent = _parse_dv(candidate)
                    consumed = 2
                elif _DASH_RE.match(candidate):
                    consumed = 2
                    if index + 2 < len(cells):
                        dv_percent = _parse_dv(cells[index + 2])
                        if dv_percent is not None:
                            consumed = 3
                elif (
                    candidate == "" and index + 2 < len(cells) and _DASH_RE.match(cells[index + 2])
                ):
                    # Current cards sometimes render an unavailable amount as
                    # an empty amount cell paired with a dash DV. This is not
                    # numeric zero; arbitrary blank/populated pairs still fail.
                    consumed = 3
                else:
                    return [], (
                        f"unparsable amount '{candidate}' for known nutrient '{cells[index]}'"
                    )
            key = NUTRIENT_KEY_BY_LABEL.get(_NUTRIENT_ALIASES.get(cells[index], cells[index]))
            rows.append(
                ParsedNutrientRow(
                    key=key,
                    raw_label=cells[index],
                    amount_text=amount_text,
                    dv_percent=dv_percent,
                )
            )
            index += consumed
        return rows, None

    @staticmethod
    def _extract_section_text(soup: BeautifulSoup, heading: str) -> str | None:
        header = soup.find(string=re.compile(rf"^{heading}$"))
        if header is not None:
            parent = header.parent
            if parent is not None:
                parts: list[str] = []
                for sib in parent.next_siblings:
                    if isinstance(sib, Tag):
                        if sib.name in {"h1", "h2"}:
                            break
                        text = sib.get_text(" ", strip=True)
                        if text:
                            parts.append(text)
                    elif isinstance(sib, str):
                        cleaned = sib.strip()
                        if cleaned:
                            parts.append(cleaned)
                if parts:
                    return "\n".join(parts).strip()

        upper = heading.upper()
        match = re.search(
            rf"{upper}\s*:?\s*\n?(.+?)(?=\n[A-Z][A-Z /&-]+:\s*\n|\nNote:|\Z)",
            soup.get_text("\n"),
            re.DOTALL,
        )
        if match:
            captured = match.group(1).strip()
            return captured or None
        return None

    @staticmethod
    def _detect_placeholder(
        ingredients_raw: str | None,
        rows: tuple[ParsedNutrientRow, ...],
        calories_text: str | None,
    ) -> bool:
        if ingredients_raw is not None and ingredients_raw.strip() == PLACEHOLDER_INGREDIENTS_TEXT:
            return True
        amounts: list[Decimal] = []
        for row in rows:
            if not row.amount_text:
                continue
            match = _AMOUNT_RE.match(row.amount_text)
            if match:
                try:
                    amounts.append(Decimal(match.group(1)))
                except InvalidOperation:
                    return False
        all_zero = bool(rows) and all(a == 0 for a in amounts)
        calories_zero = calories_text is not None and calories_text.strip() in {"0", ""}
        return bool(all_zero and calories_zero and not ingredients_raw)
