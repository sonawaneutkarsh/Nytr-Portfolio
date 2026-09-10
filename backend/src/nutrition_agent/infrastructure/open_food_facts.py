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
    BarcodeNutritionBasis,
    BarcodeProduct,
    BarcodeProductIncomplete,
    BarcodeProductNotFound,
    BarcodeProviderUnavailable,
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
_FIELDS = "code,product_name,brands,serving_size,nutrition"
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
        raise BarcodeProductIncomplete("barcode product has no nutrition object")
    basis, source_set, values = _select_exact_nutrition(nutrition_object, serving_size)
    try:
        nutrition = ManualNutritionFacts(**values)
    except (TypeError, ValueError) as exc:
        raise BarcodeProductIncomplete("barcode product has no usable exact nutrition") from exc

    evidence = {
        "code": code,
        "product_name": name,
        "brands": brand,
        "serving_size": serving_size,
        "basis": basis.value,
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
    if basis is BarcodeNutritionBasis.PER_SERVING:
        if serving_size is None:
            raise BarcodeProductIncomplete("per-serving nutrition has no serving description")
        description = serving_size
    else:
        description = _basis_description(basis)
    amount, unit = _basis_amount_and_unit(basis)
    return BarcodeProduct(
        policy_version=BARCODE_IMPORT_POLICY_VERSION,
        name=name,
        brand=brand,
        serving_description=description,
        serving_amount=amount,
        serving_unit=unit,
        nutrition=nutrition,
        provenance=provenance,
    )


def _select_exact_nutrition(
    nutrition: Mapping[object, object], serving_size: str | None
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
    # Standardized packaging sets remain tied to their explicit mass/volume basis.
    # Some provider records duplicate standardized values into a serving input set,
    # so serving is a fail-closed fallback rather than a preferred source.
    ordered_bases = [BarcodeNutritionBasis.PER_100G, BarcodeNutritionBasis.PER_100ML]
    if serving_size is not None:
        ordered_bases.append(BarcodeNutritionBasis.PER_SERVING)
    source_per = {
        BarcodeNutritionBasis.PER_SERVING: "serving",
        BarcodeNutritionBasis.PER_100G: "100g",
        BarcodeNutritionBasis.PER_100ML: "100ml",
    }
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
        if not isinstance(raw, Mapping):
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
    return "100 ml" if basis is BarcodeNutritionBasis.PER_100ML else "100 g"


def _basis_amount_and_unit(basis: BarcodeNutritionBasis) -> tuple[str, str]:
    if basis is BarcodeNutritionBasis.PER_SERVING:
        return "1", "serving"
    return "100", "ml" if basis is BarcodeNutritionBasis.PER_100ML else "g"


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
