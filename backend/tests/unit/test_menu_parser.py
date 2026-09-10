from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from nutrition_agent.domain.stacks.entities import (
    MealPeriod,
)
from nutrition_agent.infrastructure.stacks_source.menu_parser import MenuPageParser

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"
SERVICE_DATE = date(2026, 8, 21)


def parse(filename: str, meal: MealPeriod = MealPeriod.LUNCH):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    return MenuPageParser().parse(html, SERVICE_DATE, meal, 50)


def test_menu_parser_happy_path_matches_expected_contract() -> None:
    result = parse("daily_menu_lunch_2026-08-21.html")
    assert result.ok and result.value is not None
    page = result.value

    expected = json.loads(
        (FIXTURES / "expected" / "daily_menu_lunch_2026-08-21.expected.json").read_text()
    )
    assert page.selection_echo_ok is True
    assert page.empty_period is False
    assert page.date_window[0] == SERVICE_DATE
    assert len(page.date_window) == 7

    got_categories = [c.name for c in page.categories]
    want_categories = [c["name"] for c in expected["categories"]]
    assert got_categories == want_categories

    got_items = [
        (item.name_raw, item.mid_instance) for cat in page.categories for item in cat.items
    ]
    want_items = [
        (i["name_raw"], i["mid_instance"]) for c in expected["categories"] for i in c["items"]
    ]
    assert got_items == want_items
    assert len(got_items) == expected["item_count"]


def test_current_menu_category_markup_matches_captured_contract() -> None:
    current_date = date(2026, 8, 27)
    html = (FIXTURES / "daily_menu_lunch_2026-08-27_current.html").read_text(encoding="utf-8")

    result = MenuPageParser().parse(html, current_date, MealPeriod.LUNCH, 50)

    assert result.ok and result.value is not None
    page = result.value
    assert page.selection_echo_ok is True
    assert page.date_window == tuple(date(2026, 8, 27 + offset) for offset in range(5)) + (
        date(2026, 9, 1),
        date(2026, 9, 2),
    )
    assert [category.name for category in page.categories] == ["SYNTHETIC SPECIALS"]
    assert [(item.name_raw, item.mid_instance) for item in page.categories[0].items] == [
        ("Sample Sandwich", "910000001"),
        ("Sample Bowl", "910000002"),
        ("Sample Pasta", "910000003"),
        ("Sample Soup", "910000004"),
    ]
    assert page.categories[0].items[0].dietary_tags[0].value == "Meatless"
    assert page.categories[0].items[1].dietary_tags == ()


def test_conflicting_supported_category_titles_fail_closed() -> None:
    html = (FIXTURES / "daily_menu_lunch_2026-08-21.html").read_text(encoding="utf-8")
    mutated = html.replace(
        '<span class="category-name">SYNTHETIC ENTREES</span>',
        '<span class="category-name">SYNTHETIC ENTREES</span>'
        '<span class="nutrition-category-title">CONFLICT</span>',
        1,
    )

    result = MenuPageParser().parse(mutated, SERVICE_DATE, MealPeriod.LUNCH, 50)

    assert not result.ok
    assert result.failure is not None
    assert "unambiguous" in result.failure.detail


def test_stacks_meal_period_enum_remains_three_published_buckets() -> None:
    assert tuple(period.value for period in MealPeriod) == ("Breakfast", "Lunch", "Dinner")


def test_duplicate_names_get_distinct_mids_and_ordinals() -> None:
    from nutrition_agent.infrastructure.stacks_source.normalizer import (
        assign_occurrence_ordinals,
        menu_day_from_parsed,
    )

    result = parse("daily_menu_lunch_2026-08-21.html")
    assert result.ok and result.value
    day = menu_day_from_parsed(result.value)
    ordinals = assign_occurrence_ordinals(day.offerings)

    demo_entrees = [
        (idx, o) for idx, o in enumerate(day.offerings) if o.name_normalized == "Demo Entree 01"
    ]
    assert len(demo_entrees) == 2
    mids = {o.source_mid for _, o in demo_entrees}
    assert mids == {"900000001", "900000002"}
    assert sorted(ordinals[idx] for idx, _ in demo_entrees) == [0, 1]


def test_empty_meal_period_is_a_state_not_an_error() -> None:
    result = parse("daily_menu_breakfast_empty_2026-08-21.html", MealPeriod.BREAKFAST)
    assert result.ok and result.value is not None
    page = result.value
    assert page.empty_period is True
    assert page.categories == ()
    assert page.selection_echo_ok is True


def test_selection_echo_mismatch_detected() -> None:
    html = (FIXTURES / "daily_menu_lunch_2026-08-21.html").read_text(encoding="utf-8")
    wrong_date = date(2026, 8, 22)
    result = MenuPageParser().parse(html, wrong_date, MealPeriod.LUNCH, 50)
    assert result.ok and result.value is not None
    assert result.value.selection_echo_ok is False


def test_structural_anchor_rename_fails_closed() -> None:
    html = (FIXTURES / "daily_menu_lunch_2026-08-21.html").read_text(encoding="utf-8")
    mutated = html.replace("daily-menu-item__link", "menu-item-link")
    result = MenuPageParser().parse(mutated, SERVICE_DATE, MealPeriod.LUNCH, 50)
    assert result.ok and result.value is not None
    # every item must surface an item-level failure; nothing silently dropped
    assert len(result.value.item_failures) == 22
    assert all(f.code == "PARSER_MARKUP_MISMATCH" for f in result.value.item_failures)


def test_redundant_name_source_removed_is_tolerated() -> None:
    html = (FIXTURES / "daily_menu_lunch_2026-08-21.html").read_text(encoding="utf-8")
    mutated = html.replace("data-menu-item-name", "data-item-name")
    result = MenuPageParser().parse(mutated, SERVICE_DATE, MealPeriod.LUNCH, 50)
    # aria-label + anchor text still agree; graceful degradation is correct here
    assert result.ok and result.value is not None
    assert result.value.item_failures == ()
    total_items = sum(len(c.items) for c in result.value.categories)
    assert total_items == 22


def test_unknown_dietary_tag_alt_text_fails_item() -> None:
    html = (FIXTURES / "daily_menu_lunch_2026-08-21.html").read_text(encoding="utf-8")
    anchor = (
        '<a class="daily-menu-item__link" '
        'href="nutrition-label.cfm?mid=900000001" '
        'aria-label="Demo Entree 01">Demo Entree 01</a>'
    )
    icon = (
        '<span class="daily-menu-item__icons">'
        '<img src="/MENUS/LegendImages/XX.gif" alt="Mystery" aria-label="Mystery" />'
        "</span>"
    )
    mutated = html.replace(anchor, anchor + icon)
    result = MenuPageParser().parse(mutated, SERVICE_DATE, MealPeriod.LUNCH, 50)
    assert result.ok and result.value is not None
    codes = [f.code for f in result.value.item_failures]
    assert "PARSER_MARKUP_MISMATCH" in codes
    details = [f.detail for f in result.value.item_failures]
    assert any("Mystery" in d for d in details)
