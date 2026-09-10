"""Shared builders for planning tests: synthetic offerings + the real weekly schedule."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from nutrition_agent.domain.nutrition.facts import ALL_NUTRIENT_KEYS, NutritionFacts
from nutrition_agent.domain.nutrition.meal import MealLine
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    DietaryTag,
    NutrientKey,
    NutritionProfile,
    Provenance,
    ServingBasisKind,
)

SCHEDULE_VERSION = "weekly-test.v1"


def make_facts(
    values: dict[NutrientKey, str] | None = None,
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
    complete: bool = True,
) -> NutritionFacts:
    quantities: dict[NutrientKey, Decimal] = {}
    if complete:
        quantities = {key: Decimal("1") for key in ALL_NUTRIENT_KEYS}
        base_calories = Decimal(400)
        base_protein = Decimal("25.0")
        quantities[NutrientKey.CALORIES_KCAL] = base_calories
        quantities[NutrientKey.PROTEIN_G] = base_protein
        quantities[NutrientKey.SODIUM_MG] = Decimal("800")
    for key, value in (values or {}).items():
        quantities[key] = Decimal(value)
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(key for key, v in quantities.items() if v == 0),
        declared_unavailable=frozenset(),
        confidence=confidence,
    )


def make_offering(
    number: int,
    *,
    name: str | None = None,
    calories: str = "400",
    protein: str = "25.0",
    sodium: str = "800",
    tags: tuple[DietaryTag, ...] = (),
    category: str = "GRILL SPECIAL",
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
    complete: bool = True,
    with_profile: bool = True,
) -> OfferingViewArgs:
    return OfferingViewArgs(
        food_number=number,
        name=name or f"Food {number}",
        calories=calories,
        protein=protein,
        sodium=sodium,
        tags=tags,
        category=category,
        confidence=confidence,
        complete=complete,
        with_profile=with_profile,
    )


class OfferingViewArgs:
    def __init__(
        self,
        food_number: int,
        name: str,
        calories: str,
        protein: str,
        sodium: str,
        tags: tuple[DietaryTag, ...],
        category: str,
        confidence: Confidence,
        complete: bool,
        with_profile: bool,
    ) -> None:
        self.food_number = food_number
        self.name = name
        self.calories = calories
        self.protein = protein
        self.sodium = sodium
        self.tags = tags
        self.category = category
        self.confidence = confidence
        self.complete = complete
        self.with_profile = with_profile

    def build(self) -> object:
        from nutrition_agent.domain.planning.menu_view import OfferingView

        profile = None
        sha = None
        if self.with_profile:
            profile = NutritionProfile(
                food_id=UUID(int=self.food_number),
                serving_basis_raw="1 SERVG",
                serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
                nutrients=_profile_nutrients(
                    {
                        NutrientKey.CALORIES_KCAL: Decimal(self.calories),
                        NutrientKey.PROTEIN_G: Decimal(self.protein),
                        NutrientKey.SODIUM_MG: Decimal(self.sodium),
                    },
                    self.complete,
                ),
                unavailable_fields=(),
                extra_fields={},
                ingredients_raw="x",
                ingredient_components=None,
                allergens=(),
                confidence=self.confidence,
                provenance=_provenance(self.food_number),
            )
            sha = f"sha-{self.food_number}"
        return OfferingView(
            offering_id=UUID(int=self.food_number * 100),
            food_id=UUID(int=self.food_number),
            name_normalized=self.name,
            source_mid=str(21580000 + self.food_number),
            occurrence_ordinal=0,
            category_name=self.category,
            dietary_tags=self.tags,
            profile=profile,
            profile_sha256=sha,
            snapshot_sha256="menu-sha",
        )

    def as_line(self) -> MealLine:
        from nutrition_agent.domain.stacks.facts_bridge import facts_from_profile

        assert self.with_profile
        profile = self.build().profile  # type: ignore[attr-defined]
        assert profile is not None
        return MealLine(
            food_id=profile.food_id,
            profile_content_sha256=f"sha-{self.food_number}",
            parser_version=profile.provenance.parser_version,
            servings=Decimal(1),
            serving_basis_raw=profile.serving_basis_raw,
            serving_basis_kind=ServingBasisKind.UNITLESS_SERVINGS,
            facts=facts_from_profile(profile),
        )


def _profile_nutrients(
    overrides: dict[NutrientKey, Decimal], complete: bool
) -> dict[NutrientKey, object]:
    from nutrition_agent.domain.stacks.entities import NUTRIENT_UNIT, NutrientValue

    out: dict[NutrientKey, object] = {}
    keys = set(ALL_NUTRIENT_KEYS) if complete else {NutrientKey.CALORIES_KCAL}
    for key in keys:
        value = overrides.get(key)
        if value is None:
            value = Decimal("1")
        out[key] = NutrientValue(value=value, unit=NUTRIENT_UNIT[key], dv_percent=None)
    return out


def _provenance(seed: int) -> Provenance:
    return Provenance(
        snapshot_id=UUID(int=seed),
        content_sha256=f"sha-{seed}",
        source_url=f"fixture://label/{seed}",
        parser_version="2026-08-21.m2.1",
        fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
    )


def block(kind: BlockKind, start: str, end: str, label: str, context: MealContext | None = None):
    return ScheduleBlock(
        kind=kind,
        start=time.fromisoformat(start),
        end=time.fromisoformat(end),
        label=label,
        meal_context=context,
    )


def real_weekly_schedule() -> WeeklySchedule:
    """Synthetic schedule used by planning tests; it contains no owner routine."""

    def _day(weekday: Weekday, blocks: tuple[ScheduleBlock, ...]) -> DaySchedule:
        return DaySchedule(weekday=weekday, blocks=blocks)

    def training(weekday: Weekday) -> DaySchedule:
        return _day(
            weekday,
            (
                block(BlockKind.HOME_PRE_WORKOUT_MEAL, "08:00", "08:30", "demo pre-workout"),
                block(BlockKind.WORKOUT, "09:00", "10:00", "demo training"),
                block(
                    BlockKind.STACKS_MEAL,
                    "10:15",
                    "11:00",
                    "demo post-workout meal",
                    context=MealContext.POST_WORKOUT_LUNCH,
                ),
                block(BlockKind.FIXED_COMMITMENT, "11:15", "12:00", "demo commitment"),
                block(
                    BlockKind.STACKS_MEAL,
                    "19:00",
                    "19:45",
                    "demo dinner",
                    context=MealContext.DINNER,
                ),
            ),
        )

    tuesday = _day(
        Weekday.TUESDAY,
        (
            block(BlockKind.HOME_PRE_WORKOUT_MEAL, "08:30", "09:00", "demo pre-workout"),
            block(BlockKind.WORKOUT, "09:30", "10:30", "demo training"),
            block(
                BlockKind.STACKS_MEAL,
                "10:45",
                "11:30",
                "demo post-workout meal",
                context=MealContext.POST_WORKOUT_LUNCH,
            ),
            block(BlockKind.FIXED_COMMITMENT, "12:00", "12:45", "demo commitment"),
        ),
    )
    thursday = _day(
        Weekday.THURSDAY,
        (block(BlockKind.FIXED_COMMITMENT, "13:00", "14:00", "demo commitment"),),
    )
    days = {
        weekday: training(weekday)
        for weekday in (Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.FRIDAY)
    }
    days.update(
        {
            Weekday.TUESDAY: tuesday,
            Weekday.THURSDAY: thursday,
            Weekday.SATURDAY: _day(Weekday.SATURDAY, ()),
            Weekday.SUNDAY: _day(Weekday.SUNDAY, ()),
        }
    )
    return WeeklySchedule(version=SCHEDULE_VERSION, timezone="UTC", days=days)


PLAN_DATE_MONDAY = date(2026, 8, 24)
PLAN_DATE_TUESDAY = date(2026, 8, 25)
PLAN_AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
