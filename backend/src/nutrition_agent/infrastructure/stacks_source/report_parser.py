"""Parser for the Interactive Nutritive Analysis report (nutrition-report-act.cfm).

Reference/cross-check use only — NOT an ingestion source path.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

from nutrition_agent.domain.stacks.parsing import ParsedReport, ParsedReportRow, ParseResult

_NUMBER_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _decimal_or_none(text: str) -> Decimal | None:
    cleaned = text.strip().rstrip("gmkc").replace(",", "")
    if not cleaned or cleaned.startswith("-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


class ReportParser:
    def parse(self, html: str) -> ParseResult[ParsedReport]:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if not isinstance(table, Tag):
            return ParseResult.err(
                code="PARSER_MARKUP_MISMATCH",
                detail="report table missing",
                selector_path="table",
            )

        rows: list[ParsedReportRow] = []
        location_label: str | None = None
        date_label: str | None = None
        header_seen = False

        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            if cells[0] == "Recipe Description":
                header_seen = True
                continue
            if not header_seen:
                if "Interactive Nutritive Analysis" in " ".join(cells):
                    continue
                joined = " ".join(cells)
                if "\u2022" in joined or "•" in joined:
                    parts = [p.strip() for p in re.split("[•·]", joined) if p.strip()]
                    if len(parts) >= 2:
                        location_label, date_label = parts[0], parts[1]
                elif location_label is None and len(cells) == 1:
                    location_label = cells[0]
                continue

            is_totals = cells[0] == "Totals"
            numeric = [_decimal_or_none(c) for c in cells[2:]]
            padded = (numeric + [None] * 14)[:14]
            rows.append(
                ParsedReportRow(
                    name=cells[0],
                    portion=cells[1] if len(cells) > 1 else "",
                    qty=padded[0],
                    calories=padded[1],
                    fiber_g=padded[2],
                    added_sugar_g=padded[3],
                    total_fat_g=padded[4],
                    protein_g=padded[5],
                    cholesterol_mg=padded[6],
                    vitamin_d_mcg=padded[7],
                    calcium_mg=padded[8],
                    iron_mg=padded[9],
                    potassium_mg=padded[10],
                    sodium_mg=padded[11],
                    saturated_fat_g=padded[12],
                    trans_fat_g=padded[13],
                    is_totals_row=is_totals,
                )
            )

        if not rows:
            return ParseResult.err(
                code="PARSER_MARKUP_MISMATCH",
                detail="no data rows parsed from report",
                selector_path="table tr",
            )
        return ParseResult.ok_result(
            ParsedReport(location_label=location_label, date_label=date_label, rows=tuple(rows))
        )
