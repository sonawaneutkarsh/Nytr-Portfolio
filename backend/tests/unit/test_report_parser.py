from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nutrition_agent.infrastructure.stacks_source.report_parser import ReportParser

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"


def test_report_totals_equal_sum_of_rows() -> None:
    html = (FIXTURES / "nutrition_report_totals_2items.html").read_text(encoding="utf-8")
    result = ReportParser().parse(html)
    assert result.ok and result.value is not None
    report = result.value

    item_rows = [r for r in report.rows if not r.is_totals_row]
    totals = next(r for r in report.rows if r.is_totals_row)

    assert len(item_rows) == 2
    assert item_rows[0].name == "Demo Protein Plate"
    assert item_rows[0].calories == Decimal("600")
    assert item_rows[0].portion == "1 SERVG"

    # server-side arithmetic reference: totals must equal the column sums
    assert totals.calories == sum((r.calories or Decimal(0)) for r in item_rows)
    assert totals.protein_g == sum((r.protein_g or Decimal(0)) for r in item_rows)
    assert totals.sodium_mg == sum((r.sodium_mg or Decimal(0)) for r in item_rows)
    assert item_rows[1].calories == Decimal("300")
