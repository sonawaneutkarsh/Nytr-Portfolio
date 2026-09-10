"""Cross-check: engine summation equals the SOURCE's own server-computed Totals row.

The report fixture (nutrition-report-act.cfm) is a TEST ORACLE ONLY — the
report parser is never used by ingestion or engine code paths.

Comparison semantics: the site's Totals row sums each column over the rows
WHERE IT PUBLISHED A VALUE (a '-----' cell contributes nothing to that
column's total). Our engine propagates missing values instead. To compare
like-for-like we therefore group rows by published-cell support per column and
sum within each support set, asserting equality with the site for every
column whose site value is present.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nutrition_agent.domain.nutrition.arithmetic import sum_facts
from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey
from nutrition_agent.infrastructure.stacks_source.report_parser import ReportParser

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"

# ParsedReportRow column -> engine NutrientKey
_COLUMN_MAP = {
    "calories": NutrientKey.CALORIES_KCAL,
    "protein_g": NutrientKey.PROTEIN_G,
    "fiber_g": NutrientKey.FIBER_G,
    "added_sugar_g": NutrientKey.ADDED_SUGARS_G,
    "total_fat_g": NutrientKey.TOTAL_FAT_G,
    "cholesterol_mg": NutrientKey.CHOLESTEROL_MG,
    "vitamin_d_mcg": NutrientKey.VITAMIN_D_MCG,
    "calcium_mg": NutrientKey.CALCIUM_MG,
    "iron_mg": NutrientKey.IRON_MG,
    "potassium_mg": NutrientKey.POTASSIUM_MG,
    "sodium_mg": NutrientKey.SODIUM_MG,
    "saturated_fat_g": NutrientKey.SATURATED_FAT_G,
    "trans_fat_g": NutrientKey.TRANS_FAT_G,
}


def _row_facts(row: object) -> NutritionFacts:
    quantities = {
        key: getattr(row, column)
        for column, key in _COLUMN_MAP.items()
        if getattr(row, column) is not None
    }
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )


def test_engine_sum_matches_site_totals_row() -> None:
    html = (FIXTURES / "nutrition_report_totals_2items.html").read_text(encoding="utf-8")
    parsed = ReportParser().parse(html)
    assert parsed.ok and parsed.value is not None
    rows = parsed.value.rows
    item_rows = [row for row in rows if not row.is_totals_row]
    totals_row = next(row for row in rows if row.is_totals_row)

    facts_per_row = [_row_facts(row) for row in item_rows]

    for column, key in _COLUMN_MAP.items():
        site_value = getattr(totals_row, column)
        if site_value is None:
            continue
        # rows where the SITE itself published this column
        supporting = [
            facts
            for facts, row in zip(facts_per_row, item_rows, strict=True)
            if getattr(row, column) is not None
        ]
        engine_total = sum_facts(supporting)
        assert engine_total.quantities[key] == site_value, column

    # headline spot-checks across full-support columns
    all_rows = sum_facts(facts_per_row)
    assert all_rows.quantities[NutrientKey.CALORIES_KCAL] == Decimal("900")
    assert all_rows.quantities[NutrientKey.PROTEIN_G] == Decimal("60")
    assert all_rows.quantities[NutrientKey.TRANS_FAT_G] == Decimal("0")
