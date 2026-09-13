"""Bounded read-only Open Food Facts adapter for exact product-code lookups."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import Lock
from urllib.parse import quote

import httpx

from nutrition_agent.domain.nutrition.barcodes import (
    BARCODE_IMPORT_POLICY_VERSION,
    BarcodeBasisReason,
    BarcodeNutritionBasis,
    BarcodeProduct,
    BarcodeProductIncomplete,
    BarcodeProductNotFound,
    BarcodeProviderUnavailable,
    display_unit,
    validate_barcode,
)
from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodAuthority,
    CustomFoodProvenance,
    ManualNutritionFacts,
)
from nutrition_agent.infrastructure.http_transport import RateLimiter

OPEN_FOOD_FACTS_PROVIDER = "open_food_facts"
OPEN_FOOD_FACTS_DATA_LICENSE = "ODbL-1.0/DbCL-1.0"
_BASE_URL = "https://world.openfoodfacts.org"
_FIELDS = (
    "code,product_name,brands,serving_size,serving_quantity,serving_quantity_unit,"
    "product_quantity,product_quantity_unit,quantity,packagings,"
    "nutrition,nutriments,nutrition_data_per"
)
_NUTRIENTS = {
    "calories_kcal": ("energy-kcal", "kcal"),
    "protein_g": ("proteins", "g"),
    "carbohydrate_g": ("carbohydrates", "g"),
    "total_fat_g": ("fat", "g"),
    "fiber_g": ("fiber", "g"),
    "sodium_mg": ("sodium", "mg"),
}


class OpenFoodFactsProvider:
    def __init__(
        self,
        user_agent: str,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        min_interval_seconds: float = 4.0,
    ) -> None:
        if (
            user_agent != user_agent.strip()
            or "/" not in user_agent
            or "(" not in user_agent
            or not user_agent.endswith(")")
            or "\r" in user_agent
            or "\n" in user_agent
        ):
            raise ValueError("Open Food Facts User-Agent must use App/Version (Contact) form")
        self._client = client or httpx.Client(
            timeout=10,
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self._clock = clock
        self._rate_limiter = RateLimiter(min_interval_seconds)
        self._request_lock = Lock()

    def lookup(self, barcode: str) -> BarcodeProduct:
        scanned = validate_barcode(barcode)
        url = f"{_BASE_URL}/api/v3.6/product/{quote(scanned, safe='')}.json"
        try:
            with self._request_lock:
                self._rate_limiter.wait_turn()
                response = self._client.get(url, params={"fields": _FIELDS})
        except httpx.HTTPError as exc:
            raise BarcodeProviderUnavailable("barcode provider request failed") from exc
        if response.status_code == 404:
            raise BarcodeProductNotFound("barcode product was not found")
        if response.status_code != 200:
            raise BarcodeProviderUnavailable(
                f"barcode provider returned HTTP {response.status_code}"
            )
        if len(response.content) > 512_000:
            raise BarcodeProviderUnavailable("barcode provider response exceeded size limit")
        try:
            payload = json.loads(response.content, parse_float=Decimal, parse_int=Decimal)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BarcodeProviderUnavailable("barcode provider returned invalid JSON") from exc
        return _parse_product(scanned, payload, self._clock())


def _parse_product(scanned: str, payload: object, fetched_at: datetime) -> BarcodeProduct:
    if not isinstance(payload, Mapping):
        raise BarcodeProviderUnavailable("barcode provider response is not an object")
    product = payload.get("product")
    if not isinstance(product, Mapping):
        raise BarcodeProductNotFound("barcode product was not found")
    code = _clean_text(product.get("code"))
    name = _clean_text(product.get("product_name"))
    if code is None or not code.isascii() or not code.isdigit() or name is None:
        raise BarcodeProductIncomplete("barcode product identity is incomplete")
    if code.lstrip("0") != scanned.lstrip("0"):
        raise BarcodeProviderUnavailable("barcode provider returned a different product code")
    brand = _clean_text(product.get("brands"))
    serving_size = _clean_text(product.get("serving_size"))
    nutrition_object = product.get("nutrition")
    if not isinstance(nutrition_object, Mapping):
        nutrition_object = _legacy_nutrition(product)
    serving = _physical_serving(product)
    package_unit = _product_physical_unit(product)
    basis, source_set, values = _select_exact_nutrition(
        nutrition_object, serving_size, serving, package_unit
    )
    # Keep the source basis explicit, but freeze the usable manufacturer serving
    # as the food's physical serving basis. No scoop/string mass inference.
    amount, unit = _basis_amount_and_unit(basis)
    description = (
        serving_size if basis is BarcodeNutritionBasis.PER_SERVING else _basis_description(basis)
    )
    reason = _standardized_basis_reason(basis)
    if serving is not None:
        physical_amount, physical_unit = serving
        if basis is BarcodeNutritionBasis.PER_SERVING or physical_unit == unit:
            factor = (
                Decimal(1)
                if basis is BarcodeNutritionBasis.PER_SERVING
                else physical_amount / Decimal(100)
            )
            values = {
                key: value * factor if value is not None else None for key, value in values.items()
            }
            amount, unit = str(physical_amount), physical_unit
            description = f"1 serving ({physical_amount} {display_unit(physical_unit)})"
            reason = (
                BarcodeBasisReason.STRUCTURED_SERVING_VOLUME
                if physical_unit == "ml"
                else BarcodeBasisReason.STRUCTURED_SERVING_MASS
            )
    try:
        nutrition = ManualNutritionFacts(**values)
    except (TypeError, ValueError) as exc:
        raise BarcodeProductIncomplete("barcode product has no usable exact nutrition") from exc

    evidence = {
        "code": code,
        "product_name": name,
        "brands": brand,
        "serving_size": serving_size,
        "product_quantity": _canonical_number(product.get("product_quantity")),
        "product_quantity_unit": _clean_text(product.get("product_quantity_unit")),
        "package_physical_unit": package_unit,
        "basis": basis.value,
        "import_policy": BARCODE_IMPORT_POLICY_VERSION,
        "serving_amount": amount,
        "serving_unit": unit,
        "source": source_set.get("source"),
        "preparation": source_set.get("preparation"),
        "nutrients": {
            source_name: _canonical_nutrient(source_set, source_name)
            for source_name, _ in _NUTRIENTS.values()
        },
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    provenance = CustomFoodProvenance(
        authority=CustomFoodAuthority.OPEN_FOOD_FACTS,
        provider=OPEN_FOOD_FACTS_PROVIDER,
        scanned_barcode=scanned,
        provider_code=code,
        product_url=f"{_BASE_URL}/product/{code}",
        fetched_at=fetched_at,
        payload_sha256=hashlib.sha256(canonical).hexdigest(),
        data_license=OPEN_FOOD_FACTS_DATA_LICENSE,
        nutrition_basis=basis.value,
    )
    if description is None:
        raise BarcodeProductIncomplete("per-serving nutrition has no serving description")
    return BarcodeProduct(
        policy_version=BARCODE_IMPORT_POLICY_VERSION,
        name=name,
        brand=brand,
        serving_description=description,
        serving_amount=amount,
        serving_unit=unit,
        nutrition=nutrition,
        provenance=provenance,
        basis_reason=reason.value,
    )


def _standardized_basis_reason(basis: BarcodeNutritionBasis) -> BarcodeBasisReason:
    """Classify why a standardized basis had to be used, with no source detail."""

    if basis is BarcodeNutritionBasis.PER_SERVING:
        return BarcodeBasisReason.SOURCE_SERVING_WITHOUT_PHYSICAL_QUANTITY
    if basis is BarcodeNutritionBasis.PER_100ML:
        return BarcodeBasisReason.EXPLICIT_PER_100ML
    return BarcodeBasisReason.NO_TRUSTWORTHY_VOLUME_EVIDENCE


def _physical_serving(product: Mapping[object, object]) -> tuple[Decimal, str] | None:
    quantity = _decimal(product.get("serving_quantity"), Decimal(1))
    unit = _normalized_unit(product.get("serving_quantity_unit"))
    if quantity is None or quantity <= 0 or unit not in {"g", "ml"}:
        return None
    if _clean_text(product.get("serving_size")) is None:
        return None
    return quantity, unit


def _product_physical_unit(product: Mapping[object, object]) -> str | None:
    """Return only a structured mass/volume hint; never parse product names or free text.

    OFF normalizes the whole-product quantity to g or ml. A structured packaging
    quantity can provide the same kind-only hint when every usable component agrees.
    The amount is deliberately not used as a serving and no density conversion occurs.
    """

    quantity = _decimal(product.get("product_quantity"), Decimal(1))
    unit = _normalized_unit(product.get("product_quantity_unit"), kind_only=True)
    if quantity is not None and quantity > 0 and unit is not None:
        return unit

    packagings = product.get("packagings")
    if not isinstance(packagings, list):
        return None
    units: set[str] = set()
    for packaging in packagings:
        if not isinstance(packaging, Mapping):
            continue
        amount = _decimal(packaging.get("quantity_per_unit_value"), Decimal(1))
        structured_unit = _normalized_unit(packaging.get("quantity_per_unit_unit"), kind_only=True)
        if amount is not None and amount > 0 and structured_unit is not None:
            units.add(structured_unit)
    return units.pop() if len(units) == 1 else None


def _legacy_nutrition(product: Mapping[object, object]) -> Mapping[object, object]:
    """OFF's documented normalized suffix values use canonical g/kcal units.

    Never read prepared/estimated fields or use contributor *_unit to reinterpret
    normalized *_100g values. This fallback is only for the legacy schema.
    """
    raw = product.get("nutriments")
    if not isinstance(raw, Mapping):
        raise BarcodeProductIncomplete("barcode product has no nutrition object")
    per = product.get("nutrition_data_per")
    if per not in {"100g", "100ml", "serving"}:
        raise BarcodeProductIncomplete("legacy nutrition basis is unspecified")
    suffix = per
    nutrients: dict[str, object] = {}
    for source_name, expected_unit in _NUTRIENTS.values():
        # _value ties the normalized value to contributed evidence, not a
        # provider estimate. Modifiers such as '<' are not exact values.
        if raw.get(f"{source_name}_value") is None or raw.get(f"{source_name}_modifier") not in (
            None,
            "",
            "=",
        ):
            continue
        value = raw.get(f"{source_name}_{suffix}")
        if value is not None:
            nutrients[source_name] = {
                "value": value,
                "unit": "g" if expected_unit == "mg" else expected_unit,
            }
    return {
        "input_sets": [
            {"source": "packaging", "preparation": "as_sold", "per": per, "nutrients": nutrients}
        ]
    }


def _select_exact_nutrition(
    nutrition: Mapping[object, object],
    serving_size: str | None,
    physical_serving: tuple[Decimal, str] | None = None,
    product_physical_unit: str | None = None,
) -> tuple[BarcodeNutritionBasis, Mapping[object, object], dict[str, Decimal | None]]:
    raw_sets = nutrition.get("input_sets")
    if not isinstance(raw_sets, list):
        raise BarcodeProductIncomplete("barcode product has no source nutrition sets")
    candidates = [
        value
        for value in raw_sets
        if isinstance(value, Mapping)
        and value.get("source") == "packaging"
        and value.get("preparation") == "as_sold"
        and isinstance(value.get("nutrients"), Mapping)
    ]
    # Prefer an explicit packaging serving only when its physical quantity agrees
    # with product serving metadata. Never combine conflicting sets or fabricate
    # protein by selecting a partial standardized set ahead of a verified serving.
    physical_unit = physical_serving[1] if physical_serving is not None else product_physical_unit
    ordered_bases = (
        [BarcodeNutritionBasis.PER_100ML, BarcodeNutritionBasis.PER_100G]
        if physical_unit == "ml"
        else [BarcodeNutritionBasis.PER_100G, BarcodeNutritionBasis.PER_100ML]
    )
    if serving_size is not None:
        ordered_bases.append(BarcodeNutritionBasis.PER_SERVING)
    source_per = {
        BarcodeNutritionBasis.PER_SERVING: "serving",
        BarcodeNutritionBasis.PER_100G: "100g",
        BarcodeNutritionBasis.PER_100ML: "100ml",
    }
    if physical_serving is not None:
        amount, unit = physical_serving
        # A source with an explicit contradictory serving quantity cannot be
        # assigned the product-level serving mass, even as a last fallback.
        candidates = [
            candidate
            for candidate in candidates
            if candidate.get("per") != "serving"
            or (candidate.get("per_quantity") is None and candidate.get("per_unit") is None)
            or (
                _decimal(candidate.get("per_quantity"), Decimal(1)) == amount
                and _normalized_unit(candidate.get("per_unit")) == unit
            )
        ]
        verified_servings = []
        for candidate in candidates:
            if candidate.get("per") != "serving":
                continue
            per_quantity = candidate.get("per_quantity")
            per_unit = candidate.get("per_unit")
            if (per_quantity is None and per_unit is None) or (
                _decimal(per_quantity, Decimal(1)) == amount and _normalized_unit(per_unit) == unit
            ):
                verified_servings.append(candidate)
        if verified_servings:
            candidates = [
                candidate for candidate in candidates if candidate.get("per") != "serving"
            ] + verified_servings
            ordered_bases = [
                BarcodeNutritionBasis.PER_SERVING,
                *ordered_bases,
            ]
    for basis in ordered_bases:
        parsed = [
            (candidate, _nutrition_values(candidate))
            for candidate in candidates
            if candidate.get("per") == source_per[basis]
        ]
        usable = [
            (candidate, values)
            for candidate, values in parsed
            if any(value is not None for value in values.values())
        ]
        if not usable:
            continue
        signatures = {tuple(values.items()) for _, values in usable}
        if len(signatures) != 1:
            raise BarcodeProductIncomplete("barcode product has ambiguous source nutrition")
        return basis, usable[0][0], usable[0][1]
    raise BarcodeProductIncomplete(
        "barcode product has no exact packaging nutrition per 100 g, 100 ml, or serving"
    )


def _nutrition_values(source_set: Mapping[object, object]) -> dict[str, Decimal | None]:
    nutrients = source_set.get("nutrients")
    if not isinstance(nutrients, Mapping):
        return {name: None for name in _NUTRIENTS}
    result: dict[str, Decimal | None] = {}
    for output_name, (source_name, expected_unit) in _NUTRIENTS.items():
        raw = nutrients.get(source_name)
        if not isinstance(raw, Mapping) or raw.get("modifier") not in (None, "", "="):
            result[output_name] = None
            continue
        unit = _clean_text(raw.get("unit"))
        multiplier = _unit_multiplier(unit, expected_unit)
        result[output_name] = (
            _decimal(raw.get("value"), multiplier) if multiplier is not None else None
        )
    return result


def _unit_multiplier(unit: str | None, expected_unit: str) -> Decimal | None:
    if expected_unit == "mg":
        return {"g": Decimal(1000), "mg": Decimal(1)}.get(unit or "")
    return Decimal(1) if unit == expected_unit else None


def _canonical_nutrient(
    source_set: Mapping[object, object], source_name: str
) -> dict[str, str | None]:
    nutrients = source_set.get("nutrients")
    raw = nutrients.get(source_name) if isinstance(nutrients, Mapping) else None
    if not isinstance(raw, Mapping):
        return {"value": None, "unit": None}
    return {
        "value": _canonical_number(raw.get("value")),
        "unit": _clean_text(raw.get("unit")),
    }


def _basis_description(basis: BarcodeNutritionBasis) -> str:
    return "100 mL" if basis is BarcodeNutritionBasis.PER_100ML else "100 g"


def _basis_amount_and_unit(basis: BarcodeNutritionBasis) -> tuple[str, str]:
    if basis is BarcodeNutritionBasis.PER_SERVING:
        return "1", "serving"
    return "100", "ml" if basis is BarcodeNutritionBasis.PER_100ML else "g"


def _normalized_unit(value: object, *, kind_only: bool = False) -> str | None:
    unit = _clean_text(value)
    if unit is None:
        return None
    normalized = unit.casefold()
    if normalized in {"ml", "milliliter", "milliliters", "cl", "l"}:
        return "ml" if kind_only or normalized == "ml" else None
    if normalized in {"g", "gram", "grams", "kg"}:
        return "g" if kind_only or normalized == "g" else None
    return None


def _decimal(value: object, multiplier: Decimal) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result * multiplier


def _canonical_number(value: object) -> str | None:
    parsed = _decimal(value, Decimal(1))
    return str(parsed) if parsed is not None else None


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


__all__ = [
    "OPEN_FOOD_FACTS_DATA_LICENSE",
    "OPEN_FOOD_FACTS_PROVIDER",
    "OpenFoodFactsProvider",
]
