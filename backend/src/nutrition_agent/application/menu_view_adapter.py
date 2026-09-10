"""Build M4 planning read models from persisted ingestion rows (M6).

Bridges persisted ingestion data (MenuOffering + NutritionProfile + food names
+ profile row-id links) into the caller-supplied ``MenuDayView`` the frozen M4
planner consumes. Availability comes ONLY from offerings of the requested
(date, period); catalog presence never implies availability (ADR-015 §3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from uuid import UUID

from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.stacks.entities import (
    MealPeriod,
    MenuOffering,
    NutritionProfile,
    NutritionSourceState,
)


def offering_profile_ids(offerings: Sequence[MenuOffering]) -> dict[str, UUID]:
    """offering_id -> linked nutrition_profile row id, only where present."""
    return {
        str(offering.offering_id): offering.profile_id
        for offering in offerings
        if offering.profile_id is not None
    }


def build_menu_day_view(
    service_date: date,
    fetched_at: datetime,
    snapshot_sha256: str,
    offerings_with_profiles: Sequence[tuple[MenuOffering, NutritionProfile | None]],
    food_names: Mapping[UUID, str],
    explicitly_empty_periods: Iterable[MealPeriod] = (),
) -> MenuDayView:
    grouped: dict[MealPeriod, list[OfferingView]] = {}
    empty_periods = [period for period in explicitly_empty_periods]
    for offering, profile in sorted(
        offerings_with_profiles,
        key=lambda pair: (
            pair[0].meal_period.value,
            pair[0].category_position,
            pair[0].item_position,
            pair[0].occurrence_ordinal,
        ),
    ):
        view = OfferingView(
            offering_id=offering.offering_id,
            food_id=offering.food_id,
            name_normalized=food_names.get(offering.food_id, ""),
            source_mid=offering.source_mid,
            occurrence_ordinal=offering.occurrence_ordinal,
            category_name=offering.category_name,
            dietary_tags=offering.dietary_tags,
            profile=profile,
            profile_sha256=profile.provenance.content_sha256 if profile else None,
            snapshot_sha256=snapshot_sha256,
            nutrition_source_state=(
                NutritionSourceState.PROFILE_AVAILABLE if profile is not None else None
            ),
            nutrition_snapshot_sha256=(profile.provenance.content_sha256 if profile else None),
        )
        grouped.setdefault(offering.meal_period, []).append(view)

    periods: dict[MealPeriod, PeriodMenu] = {
        period: PeriodMenu(offerings=tuple(views), explicitly_empty=False)
        for period, views in sorted(grouped.items(), key=lambda item: item[0].value)
    }
    for period in empty_periods:
        if period not in periods:
            periods[period] = PeriodMenu(offerings=(), explicitly_empty=True)
    return MenuDayView(
        service_date=service_date,
        periods=periods,
        fetched_at=fetched_at,
        snapshot_sha256=snapshot_sha256,
    )


__all__ = ["build_menu_day_view", "offering_profile_ids"]
