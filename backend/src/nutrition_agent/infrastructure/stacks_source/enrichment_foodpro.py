"""OPTIONAL FoodPro (Source B) enrichment mapper — enrichment/research ONLY.

Per ADR-012: never a production dependency; failures must not affect Source A
ingestion. This module is inert unless explicitly enabled by the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from nutrition_agent.infrastructure.stacks_source.normalizer import normalize_name

_RECNUM_RE = re.compile(r"RecNumAndPort=(\d+)\*(\d+)")


@dataclass(frozen=True)
class FoodProItemRef:
    name_normalized: str
    rec_num: str | None
    portion: str | None


def _item_name(desc: Tag) -> tuple[str, str | None]:
    """Return (name_text, portion_text) with the portion span excluded from the name."""
    portion_el = desc.find(class_="portion")
    portion_text: str | None = None
    if isinstance(portion_el, Tag):
        portion_text = " ".join(portion_el.get_text().replace("\xa0", " ").split())
    anchor = desc.find("a", href=True)
    if isinstance(anchor, Tag):
        return " ".join(anchor.get_text().split()), portion_text
    text = " ".join(desc.get_text().replace("\xa0", " ").split())
    if portion_text:
        text = text.replace(portion_text, "").strip()
    return text, portion_text


def extract_item_refs(longmenu_html: str) -> dict[str, FoodProItemRef]:
    """Extract name -> (rec_num, portion) refs from a longmenu.aspx page.

    Items without a captured label link yield rec_num=None. Later occurrences
    with an ID win over earlier ones without one.
    """
    soup = BeautifulSoup(longmenu_html, "lxml")
    refs: dict[str, FoodProItemRef] = {}
    for desc in soup.find_all(class_="menudesc"):
        if not isinstance(desc, Tag):
            continue
        name_text, portion_text = _item_name(desc)
        if not name_text:
            continue
        name = normalize_name(name_text)
        rec_num: str | None = None
        anchor = desc.find("a", href=True)
        if isinstance(anchor, Tag):
            match = _RECNUM_RE.search(str(anchor.get("href") or ""))
            if match:
                rec_num = match.group(1)
        existing = refs.get(name)
        if existing is None or (existing.rec_num is None and rec_num is not None):
            refs[name] = FoodProItemRef(name_normalized=name, rec_num=rec_num, portion=portion_text)
    return refs


def extract_component_names(longmenu_html: str, category_header: str) -> list[str]:
    """Return normalized item names under one '-- Category --' section."""
    soup = BeautifulSoup(longmenu_html, "lxml")
    names: list[str] = []
    current_section: str | None = None
    for el in soup.find_all("div"):
        classes = el.get("class") or []
        if "nutmenucats" in classes:
            current_section = " ".join(el.get_text().split()).strip("- ")
        elif "menudesc" in classes and current_section == category_header:
            name_text, _portion = _item_name(el)
            if name_text:
                names.append(normalize_name(name_text))
    return names
