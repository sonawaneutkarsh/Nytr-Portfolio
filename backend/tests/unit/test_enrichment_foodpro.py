from __future__ import annotations

from pathlib import Path

from nutrition_agent.infrastructure.stacks_source.enrichment_foodpro import (
    extract_component_names,
    extract_item_refs,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"


def test_foodpro_mapper_extracts_verified_recnums_only() -> None:
    html = (FIXTURES / "foodpro_longmenu_halal_components.html").read_text(encoding="utf-8")
    refs = extract_item_refs(html)

    assert refs["Demo Protein Plate"].rec_num == "900101"
    assert refs["Demo Configurable Bowl"].rec_num == "900102"
    assert refs["Demo Roast Plate"].rec_num == "900103"
    assert refs["Demo Falafel"].rec_num == "900104"
    assert refs["Demo Egg Slices"].rec_num == "900105"
    assert refs["Demo Hummus"].rec_num == "900106"
    assert refs["Demo Grain"].rec_num == "900107"
    assert refs["Demo Panini"].rec_num == "900108"
    assert refs["Demo Bowl"].rec_num == "900109"

    # items whose RecNum was never captured must be present WITHOUT invented IDs
    assert refs["Demo Missing A"].rec_num is None
    assert refs["Demo Missing B"].rec_num is None


def test_component_inventory_has_24_entries() -> None:
    html = (FIXTURES / "foodpro_longmenu_halal_components.html").read_text(encoding="utf-8")
    names = extract_component_names(html, "Demo Bowl Components")
    assert len(names) == 24
    assert "Demo Component 01" in names
    assert "Demo Component 24" in names
