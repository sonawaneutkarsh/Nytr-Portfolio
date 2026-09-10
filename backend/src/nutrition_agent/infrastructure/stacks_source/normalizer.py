"""Pure normalization: parsed structures to domain entities.

Also owns name normalization and occurrence-ordinal assignment rules
(docs/STACKS_DISCOVERY.md section 5 identity model).
"""

from __future__ import annotations

import html
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from uuid import UUID

from nutrition_agent.domain.stacks.entities import (
    NUTRIENT_UNIT,
    ComponentStatement,
    Confidence,
    NormalizedMenuDay,
    NormalizedOfferingInput,
    NutrientKey,
    NutrientValue,
    NutritionProfile,
    Provenance,
    ServingBasisKind,
)
from nutrition_agent.domain.stacks.parsing import ParsedLabel, ParsedMenuPage

_WHITESPACE_RE = re.compile(r"\s+")
_SERVING_UNITLESS_RE = re.compile(r"^\d+\s+SERVG$", re.IGNORECASE)


def normalize_name(raw: str) -> str:
    unescaped = html.unescape(raw)
    nfc = unicodedata.normalize("NFC", unescaped)
    return _WHITESPACE_RE.sub(" ", nfc).strip()


def assign_occurrence_ordinals(
    offerings: tuple[NormalizedOfferingInput, ...],
) -> dict[int, int]:
    """Map offering list index -> ordinal among same-name items for the day/meal."""
    seen: dict[str, int] = {}
    ordinals: dict[int, int] = {}
    ordered = sorted(
        enumerate(offerings),
        key=lambda pair: (pair[1].category_position, pair[1].item_position),
    )
    for index, offering in ordered:
        next_ordinal = seen.get(offering.name_normalized, 0)
        ordinals[index] = next_ordinal
        seen[offering.name_normalized] = next_ordinal + 1
    return ordinals


def menu_day_from_parsed(parsed: ParsedMenuPage) -> NormalizedMenuDay:
    inputs: list[NormalizedOfferingInput] = []
    for category in parsed.categories:
        for item in category.items:
            inputs.append(
                NormalizedOfferingInput(
                    service_date=parsed.requested_date,
                    meal_period=parsed.requested_meal,
                    campus_id=parsed.campus_id,
                    name_raw=item.name_raw,
                    name_normalized=normalize_name(item.name_raw),
                    source_mid=item.mid_instance,
                    dietary_tags=item.dietary_tags,
                    category_name=category.name,
                    category_position=category.position,
                    item_position=item.item_position,
                )
            )
    return NormalizedMenuDay(
        service_date=parsed.requested_date,
        meal_period=parsed.requested_meal,
        campus_id=parsed.campus_id,
        offerings=tuple(inputs),
        empty_period=parsed.empty_period and not inputs,
    )


_AMOUNT_RE = re.compile(r"^(\d+(?:\.\d+)?)(g|mg|mcg)$")


def _decimal_amount(amount_text: str) -> Decimal | None:
    match = _AMOUNT_RE.match(amount_text)
    if match is None:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def _unwrap_single_wrapper(raw: str) -> str:
    """If the entire payload is 'Name ( ... )', return the inner text."""
    start = raw.find("(")
    if start <= 0:
        return raw
    depth = 0
    for index in range(start, len(raw)):
        if raw[index] == "(":
            depth += 1
        elif raw[index] == ")":
            depth -= 1
            if depth == 0:
                if index == len(raw) - 1:
                    return raw[start + 1 : index]
                return raw
    return raw


def split_ingredient_components(raw: str) -> tuple[ComponentStatement, ...] | None:
    """Derived best-effort split of the verbatim ingredient string (marked derived)."""
    if not raw.strip():
        return None
    working = _unwrap_single_wrapper(raw.strip())
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in working:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)

    components: list[ComponentStatement] = []
    for part in parts:
        if not part:
            continue
        paren_index = part.find("(")
        if paren_index > 0:
            components.append(
                ComponentStatement(component_name=part[:paren_index].strip(), text=part.strip())
            )
        else:
            components.append(ComponentStatement(component_name=part, text=part))
    return tuple(components)


def profile_from_parsed(
    parsed: ParsedLabel,
    food_id: UUID,
    provenance: Provenance,
) -> NutritionProfile:
    """Build a domain NutritionProfile from a validated non-placeholder label parse."""
    nutrients: dict[NutrientKey, NutrientValue] = {}
    unavailable: list[NutrientKey] = []
    extra_fields: dict[str, str] = {}

    if parsed.calories_text is not None and parsed.calories_text.strip():
        try:
            nutrients[NutrientKey.CALORIES_KCAL] = NutrientValue(
                value=Decimal(parsed.calories_text.replace(",", "")),
                unit=NUTRIENT_UNIT[NutrientKey.CALORIES_KCAL],
                dv_percent=None,
            )
        except InvalidOperation:
            pass
    else:
        unavailable.append(NutrientKey.CALORIES_KCAL)

    for row in parsed.rows:
        value: Decimal | None = None
        if row.amount_text is not None and row.amount_text:
            if row.amount_text.endswith("%"):
                value = None
            else:
                value = _decimal_amount(row.amount_text)
                if value is None:
                    value = None
        if row.key is None:
            if row.amount_text:
                extra_fields[row.raw_label] = row.amount_text
            continue
        if value is None:
            unavailable.append(row.key)
        else:
            nutrients[row.key] = NutrientValue(
                value=value,
                unit=NUTRIENT_UNIT[row.key],
                dv_percent=row.dv_percent,
            )

    serving_raw = parsed.serving_basis_raw or ""
    serving_kind = (
        ServingBasisKind.UNITLESS_SERVINGS
        if _SERVING_UNITLESS_RE.match(serving_raw)
        else ServingBasisKind.UNKNOWN
    )

    return NutritionProfile(
        food_id=food_id,
        serving_basis_raw=serving_raw,
        serving_basis_kind=serving_kind,
        nutrients=nutrients,
        unavailable_fields=tuple(unavailable),
        extra_fields=extra_fields,
        ingredients_raw=parsed.ingredients_raw or "",
        ingredient_components=split_ingredient_components(parsed.ingredients_raw or ""),
        allergens=parsed.allergens,
        confidence=Confidence.OFFICIAL_PUBLISHED,
        provenance=provenance,
    )
