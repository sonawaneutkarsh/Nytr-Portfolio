"""Application-level assembly of deterministic daily-plan inputs.

This module selects versioned server configuration and combines it with an
already-resolved menu read. It intentionally performs no planning, target
production math, or SQL access.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, time
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID

from nutrition_agent.application.daily_plan import DailyPlanInputs
from nutrition_agent.application.ports import MenuDayReadRepository
from nutrition_agent.domain.configurable_meals import ConfigurableMealDefinition, EstimateState
from nutrition_agent.domain.nutrition.targets import (
    GoalKind,
    NutrientGoal,
    TargetSet,
)
from nutrition_agent.domain.owner_meal_config import (
    OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION,
    OWNER_USUAL_HALAL_BOWL_DEFINITION,
)
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.dietary import (
    AnimalSource,
    AnimalSourceEvidence,
    AnimalSourceRule,
    DietaryPolicy,
)
from nutrition_agent.domain.planning.policy import (
    EstimatedMealPolicy,
    OfferingPreference,
    PlannerPolicy,
    SlotPolicy,
    UnsupportedEstimatedTargetBehavior,
)
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    ScheduleException,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey


@dataclass(frozen=True)
class ServerInputsConfiguration:
    """Versioned schedule and planner inputs selected by the server."""

    schedule: WeeklySchedule
    schedule_exceptions: Sequence[ScheduleException]
    planner_policy: PlannerPolicy
    slot_policies: Mapping[MealContext, SlotPolicy]
    configurable_meal_definitions: Sequence[ConfigurableMealDefinition] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "schedule_exceptions", tuple(self.schedule_exceptions))
        object.__setattr__(self, "slot_policies", MappingProxyType(dict(self.slot_policies)))
        object.__setattr__(
            self, "configurable_meal_definitions", tuple(self.configurable_meal_definitions)
        )


class ServerInputsProvider(Protocol):
    """Assemble authoritative inputs without invoking the planner."""

    def build(
        self,
        *,
        user_id: UUID,
        requested_for_date: date,
        timezone: str,
        targets: TargetSet,
        target_policy_version_id: UUID | None = None,
    ) -> DailyPlanInputs: ...


class MenuDayUnavailableError(LookupError):
    """The menu read boundary cannot resolve the requested date."""


def _canonical_m6_demo_configuration() -> ServerInputsConfiguration:
    lunch_only = DaySchedule(
        weekday=Weekday.FRIDAY,
        blocks=(
            ScheduleBlock(
                kind=BlockKind.STACKS_MEAL,
                start=time(12, 30),
                end=time(13, 15),
                label="stacks post-workout lunch",
                meal_context=MealContext.POST_WORKOUT_LUNCH,
            ),
        ),
    )
    schedule = WeeklySchedule(
        version="m6-demo.v1",
        timezone="America/New_York",
        days={
            weekday: DaySchedule(weekday=weekday, blocks=())
            for weekday in Weekday
            if weekday is not Weekday.FRIDAY
        }
        | {Weekday.FRIDAY: lunch_only},
    )
    planner_policy = PlannerPolicy(
        version="demo-planner.v1",
        context_period={MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH},
        slot_shares={},
    )
    slot_policies = {
        context: SlotPolicy(context=context)
        for context in (MealContext.POST_WORKOUT_LUNCH, MealContext.DINNER)
    }
    return ServerInputsConfiguration(
        schedule=schedule,
        schedule_exceptions=(),
        planner_policy=planner_policy,
        slot_policies=slot_policies,
    )


CANONICAL_M6_DEMO_CONFIGURATION: Final = _canonical_m6_demo_configuration()
CANONICAL_M6_DEMO_TARGETS: Final = TargetSet(
    policy_version="m6-demo-targets.v1",
    goals={
        NutrientKey.CALORIES_KCAL: NutrientGoal(
            kind=GoalKind.TARGET, value=Decimal("900"), weight=Decimal("1")
        ),
        NutrientKey.PROTEIN_G: NutrientGoal(
            kind=GoalKind.TARGET, value=Decimal("50"), weight=Decimal("1")
        ),
    },
)


def _block(
    kind: BlockKind,
    start: time,
    end: time,
    label: str,
    meal_context: MealContext | None = None,
) -> ScheduleBlock:
    return ScheduleBlock(
        kind=kind,
        start=start,
        end=end,
        label=label,
        meal_context=meal_context,
    )


def _training_day(weekday: Weekday) -> DaySchedule:
    """Synthetic portfolio schedule used when running without owner data."""
    return DaySchedule(
        weekday=weekday,
        blocks=(
            _block(
                BlockKind.HOME_PRE_WORKOUT_MEAL,
                time(8),
                time(8, 30),
                "demo pre-workout meal",
            ),
            _block(BlockKind.WORKOUT, time(9), time(10), "demo training block"),
            _block(
                BlockKind.STACKS_MEAL,
                time(10, 15),
                time(11),
                "demo post-workout meal",
                MealContext.POST_WORKOUT_LUNCH,
            ),
            _block(BlockKind.FIXED_COMMITMENT, time(11, 15), time(12), "demo commitment"),
            _block(
                BlockKind.STACKS_MEAL,
                time(19),
                time(19, 45),
                "demo dinner",
                MealContext.DINNER,
            ),
        ),
    )


def _flexible_day(weekday: Weekday) -> DaySchedule:
    """Broad user-availability windows, never source service-hour claims."""

    return DaySchedule(
        weekday=weekday,
        blocks=(
            _block(
                BlockKind.STACKS_MEAL,
                time(11),
                time(16),
                "flexible Stacks lunch",
                MealContext.LUNCH,
            ),
            _block(
                BlockKind.STACKS_MEAL,
                time(16),
                time(22),
                "flexible Stacks dinner",
                MealContext.DINNER,
            ),
        ),
    )


def _production_schedule() -> WeeklySchedule:
    """Synthetic default schedule; owner deployments supply their own rules."""
    tuesday = DaySchedule(
        weekday=Weekday.TUESDAY,
        blocks=(
            _block(
                BlockKind.HOME_PRE_WORKOUT_MEAL,
                time(8, 30),
                time(9),
                "demo pre-workout meal",
            ),
            _block(BlockKind.WORKOUT, time(9, 30), time(10, 30), "demo training block"),
            _block(
                BlockKind.STACKS_MEAL,
                time(10, 45),
                time(11, 30),
                "demo post-workout meal",
                MealContext.POST_WORKOUT_LUNCH,
            ),
            _block(BlockKind.FIXED_COMMITMENT, time(12), time(12, 45), "demo commitment"),
            _block(
                BlockKind.STACKS_MEAL,
                time(19),
                time(19, 45),
                "demo dinner",
                MealContext.DINNER,
            ),
        ),
    )
    thursday = DaySchedule(
        weekday=Weekday.THURSDAY,
        blocks=(
            _block(BlockKind.FIXED_COMMITMENT, time(13), time(14), "demo commitment"),
            _block(
                BlockKind.STACKS_MEAL,
                time(14),
                time(16),
                "flexible demo lunch",
                MealContext.LUNCH,
            ),
            _block(
                BlockKind.STACKS_MEAL,
                time(16),
                time(22),
                "flexible demo dinner",
                MealContext.DINNER,
            ),
        ),
    )
    return WeeklySchedule(
        version="psh-fall-2026.v1",
        timezone="America/New_York",
        days={
            Weekday.MONDAY: _training_day(Weekday.MONDAY),
            Weekday.TUESDAY: tuesday,
            Weekday.WEDNESDAY: _training_day(Weekday.WEDNESDAY),
            Weekday.THURSDAY: thursday,
            Weekday.FRIDAY: _training_day(Weekday.FRIDAY),
            Weekday.SATURDAY: _flexible_day(Weekday.SATURDAY),
            Weekday.SUNDAY: _flexible_day(Weekday.SUNDAY),
        },
    )


def _production_context_periods() -> Mapping[MealContext, MealPeriod]:
    return {
        MealContext.LUNCH: MealPeriod.LUNCH,
        MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH,
        MealContext.DINNER: MealPeriod.DINNER,
    }


def _production_slot_policies() -> Mapping[MealContext, SlotPolicy]:
    return {
        MealContext.LUNCH: SlotPolicy(context=MealContext.LUNCH),
        MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH),
        MealContext.DINNER: SlotPolicy(
            context=MealContext.DINNER,
            portable_preferred=True,
        ),
    }


def _production_server_configuration_v1() -> ServerInputsConfiguration:
    """Historical production policy retained byte-for-byte in meaning."""

    return ServerInputsConfiguration(
        schedule=_production_schedule(),
        schedule_exceptions=(),
        planner_policy=PlannerPolicy(
            version="psh-fall-2026-planner.v1",
            context_period=_production_context_periods(),
            slot_shares={},
        ),
        slot_policies=_production_slot_policies(),
    )


def _production_planner_preferences() -> tuple[OfferingPreference, ...]:
    return (
        OfferingPreference(
            preference_id="owner.cyo_halal_bowl",
            normalized_name_aliases=frozenset({"CYO Halal Bowl"}),
            bonus=Decimal("0.05"),
        ),
        OfferingPreference(
            preference_id="owner.deli_chicken_provolone",
            normalized_name_aliases=frozenset(
                {
                    "#7 Roast Chicken & Provolone",
                    "#7 Roast Chicken & Provolone Bowl",
                    "Roast Chicken & Provolone Panini",
                }
            ),
            bonus=Decimal("0.03"),
        ),
    )


def _production_server_configuration_v2() -> ServerInputsConfiguration:
    planner_policy = PlannerPolicy(
        version="psh-fall-2026-planner.v2",
        context_period=_production_context_periods(),
        slot_shares={
            MealContext.LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.35")},
            MealContext.POST_WORKOUT_LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
            MealContext.DINNER: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
        },
        offering_preferences=_production_planner_preferences(),
    )
    return ServerInputsConfiguration(
        schedule=_production_schedule(),
        schedule_exceptions=(),
        planner_policy=planner_policy,
        slot_policies=_production_slot_policies(),
    )


def _production_server_configuration_v3() -> ServerInputsConfiguration:
    definition = OWNER_USUAL_HALAL_BOWL_DEFINITION
    planner_policy = PlannerPolicy(
        version="psh-fall-2026-planner.v3",
        context_period=_production_context_periods(),
        slot_shares={
            MealContext.LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.35")},
            MealContext.POST_WORKOUT_LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
            MealContext.DINNER: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
        },
        offering_preferences=_production_planner_preferences(),
        estimated_meal_policy=EstimatedMealPolicy(
            allowlisted_configuration_identities=frozenset({definition.allowlist_identity}),
            permitted_estimate_states=frozenset(
                {EstimateState.COMPLETE_ESTIMATE, EstimateState.PARTIAL_ESTIMATE}
            ),
            required_known_dimensions=frozenset({NutrientKey.CALORIES_KCAL, NutrientKey.PROTEIN_G}),
            permitted_scored_dimensions=frozenset(
                {NutrientKey.CALORIES_KCAL, NutrientKey.PROTEIN_G}
            ),
            unsupported_target_behavior=UnsupportedEstimatedTargetBehavior.REJECT_CANDIDATE,
            uncertainty_penalty=Decimal("0.10"),
            pairing_allowed=False,
        ),
    )
    return ServerInputsConfiguration(
        schedule=_production_schedule(),
        schedule_exceptions=(),
        planner_policy=planner_policy,
        slot_policies=_production_slot_policies(),
        configurable_meal_definitions=(definition,),
    )


def _production_dietary_policy_v1() -> DietaryPolicy:
    exact_source = AnimalSourceEvidence.EXACT_SOURCE_NAME
    owner_configuration = AnimalSourceEvidence.OWNER_CONFIGURATION
    return DietaryPolicy(
        version="owner-no-beef-pork.v1",
        disallowed_animal_sources=frozenset({AnimalSource.BEEF, AnimalSource.PORK}),
        exact_name_rules={
            # Beef is not represented by a Stacks dietary tag. These are exact
            # source-published or physically observed names, not fuzzy aliases.
            "Southwestern Flank Steak": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "Pittsburgh Flank Steak Salad": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "Beef Hot Dog": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "Beef Hot Dog's": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "#13 Beef Gyro": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "#2 Roast Beef & Cheddar": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "Roast Beef & Cheddar Panini": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            "Halal Beef Smash Burger": AnimalSourceRule(AnimalSource.BEEF, exact_source),
            # These generic burger names do not establish an animal source.
            # They are exact known ambiguous cases and therefore fail closed.
            "Double Cheese Burger": AnimalSourceRule(AnimalSource.UNKNOWN, exact_source),
            "Double Cheeseburger": AnimalSourceRule(AnimalSource.UNKNOWN, exact_source),
            "Hamburger Single": AnimalSourceRule(AnimalSource.UNKNOWN, exact_source),
            # Exact safe exceptions prevent the narrow generic-burger rule
            # from rejecting source-evidenced poultry/plant choices.
            "Turkey Burger": AnimalSourceRule(AnimalSource.TURKEY, exact_source),
            "Beyond Burger": AnimalSourceRule(AnimalSource.PLANT, exact_source),
            "Beyond Cheeseburger": AnimalSourceRule(AnimalSource.PLANT, exact_source),
            "CYO Halal Bowl": AnimalSourceRule(AnimalSource.CHICKEN, owner_configuration),
        },
        ambiguous_animal_tokens=frozenset({"burger", "cheeseburger", "hamburger"}),
    )


def _production_server_configuration_v4() -> ServerInputsConfiguration:
    definitions = (
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION,
    )
    planner_policy = PlannerPolicy(
        version="psh-fall-2026-planner.v4",
        context_period=_production_context_periods(),
        slot_shares={
            MealContext.LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.35")},
            MealContext.POST_WORKOUT_LUNCH: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
            MealContext.DINNER: {NutrientKey.CALORIES_KCAL: Decimal("0.40")},
        },
        offering_preferences=_production_planner_preferences(),
        estimated_meal_policy=EstimatedMealPolicy(
            allowlisted_configuration_identities=frozenset(
                definition.allowlist_identity for definition in definitions
            ),
            permitted_estimate_states=frozenset(
                {EstimateState.COMPLETE_ESTIMATE, EstimateState.PARTIAL_ESTIMATE}
            ),
            required_known_dimensions=frozenset({NutrientKey.CALORIES_KCAL, NutrientKey.PROTEIN_G}),
            permitted_scored_dimensions=frozenset(
                {NutrientKey.CALORIES_KCAL, NutrientKey.PROTEIN_G}
            ),
            unsupported_target_behavior=UnsupportedEstimatedTargetBehavior.REJECT_CANDIDATE,
            uncertainty_penalty=Decimal("0.10"),
            pairing_allowed=False,
        ),
        dietary_policy=_production_dietary_policy_v1(),
    )
    return ServerInputsConfiguration(
        schedule=_production_schedule(),
        schedule_exceptions=(),
        planner_policy=planner_policy,
        slot_policies=_production_slot_policies(),
        configurable_meal_definitions=definitions,
    )


def _production_dietary_policy_v2() -> DietaryPolicy:
    exact_source = AnimalSourceEvidence.EXACT_SOURCE_NAME
    owner_configuration = AnimalSourceEvidence.OWNER_CONFIGURATION
    historical = _production_dietary_policy_v1()
    exact_rules = dict(historical.exact_name_rules)
    exact_rules.update(
        {
            # Retained/live names whose source identity explicitly establishes
            # an allowed animal source or non-meat classification.
            "Chicken Tender and Ranch Snack Wrap": AnimalSourceRule(
                AnimalSource.CHICKEN, exact_source
            ),
            "Buffalo Chicken Wrap": AnimalSourceRule(AnimalSource.CHICKEN, exact_source),
            "#7 Roast Chicken & Provolone": AnimalSourceRule(AnimalSource.CHICKEN, exact_source),
            "#7 Roast Chicken & Provolone Bowl": AnimalSourceRule(
                AnimalSource.CHICKEN, exact_source
            ),
            "Roast Chicken & Provolone Panini": AnimalSourceRule(
                AnimalSource.CHICKEN, exact_source
            ),
            "#6 Tuna Salad & American Cheese": AnimalSourceRule(AnimalSource.TUNA, exact_source),
            "Cajun Seafood Pasta": AnimalSourceRule(AnimalSource.SEAFOOD, exact_source),
            "Roasted Corn & Shrimp Chowder": AnimalSourceRule(AnimalSource.SHRIMP, exact_source),
            "#8 Veggie": AnimalSourceRule(AnimalSource.PLANT, exact_source),
            "Three Cheese Egg Bite": AnimalSourceRule(AnimalSource.EGG, exact_source),
            "Vegetable & Cheese Egg Bite": AnimalSourceRule(AnimalSource.EGG, exact_source),
            "CYO Halal Bowl": AnimalSourceRule(AnimalSource.CHICKEN, owner_configuration),
        }
    )
    return DietaryPolicy(
        version="owner-chicken-seafood-veg.v1",
        allowed_animal_sources=frozenset(
            {
                AnimalSource.CHICKEN,
                AnimalSource.FISH,
                AnimalSource.SEAFOOD,
                AnimalSource.SHRIMP,
                AnimalSource.TUNA,
            }
        ),
        disallowed_animal_sources=frozenset(
            {
                AnimalSource.BEEF,
                AnimalSource.PORK,
                AnimalSource.TURKEY,
                AnimalSource.LAMB,
                AnimalSource.MUTTON,
                AnimalSource.GOAT,
                AnimalSource.OTHER_MAMMAL,
            }
        ),
        exact_name_rules=exact_rules,
        ambiguous_animal_tokens=frozenset(
            {
                "bacon",
                "blt",
                "burger",
                "cheeseburger",
                "ham",
                "hamburger",
                "meat",
                "meatball",
                "pepperoni",
                "salami",
                "sausage",
                "steak",
            }
        ),
        ambiguous_source_tokens=frozenset(
            {"bacon", "ham", "meat", "meatball", "pepperoni", "salami", "sausage", "steak"}
        ),
    )


def _production_server_configuration_v5() -> ServerInputsConfiguration:
    historical = _production_server_configuration_v4()
    return replace(
        historical,
        planner_policy=replace(
            historical.planner_policy,
            version="psh-fall-2026-planner.v5",
            dietary_policy=_production_dietary_policy_v2(),
        ),
    )


PRODUCTION_SERVER_CONFIGURATION_V1: Final = _production_server_configuration_v1()
PRODUCTION_SERVER_CONFIGURATION_V2: Final = _production_server_configuration_v2()
PRODUCTION_SERVER_CONFIGURATION_V3: Final = _production_server_configuration_v3()
PRODUCTION_SERVER_CONFIGURATION_V4: Final = _production_server_configuration_v4()
PRODUCTION_SERVER_CONFIGURATION: Final = _production_server_configuration_v5()


def required_menu_periods(configuration: ServerInputsConfiguration) -> tuple[MealPeriod, ...]:
    """Return only periods reachable from this versioned planner policy.

    Home meals never enter the Stacks planner.  Requiring an unrelated source
    period would make valid plan inputs unavailable without changing any
    actual slot decision.
    """

    requested = frozenset(configuration.planner_policy.context_period.values())
    return tuple(period for period in MealPeriod if period in requested)


@dataclass(frozen=True)
class DefaultServerInputsProvider:
    """Default input assembler backed by a narrow resolved-menu read port."""

    menu_days: MenuDayReadRepository
    configuration: ServerInputsConfiguration = field(
        default_factory=lambda: PRODUCTION_SERVER_CONFIGURATION
    )

    def build(
        self,
        *,
        user_id: UUID,
        requested_for_date: date,
        timezone: str,
        targets: TargetSet,
        target_policy_version_id: UUID | None = None,
    ) -> DailyPlanInputs:
        resolved_menu = self.menu_days.get_for_date(requested_for_date)
        if resolved_menu is None:
            raise MenuDayUnavailableError(f"no resolved menu for {requested_for_date.isoformat()}")
        if resolved_menu.menu.service_date != requested_for_date:
            raise MenuDayUnavailableError("resolved menu date does not match the requested date")

        return DailyPlanInputs(
            user_id=user_id,
            requested_for_date=requested_for_date,
            timezone=timezone,
            schedule=self.configuration.schedule,
            exceptions=self.configuration.schedule_exceptions,
            menu=resolved_menu.menu,
            policy=self.configuration.planner_policy,
            slot_policies=self.configuration.slot_policies,
            targets=targets,
            target_policy_version_id=target_policy_version_id,
            offering_profile_ids=resolved_menu.offering_profile_ids,
            configurable_meal_definitions=self.configuration.configurable_meal_definitions,
        )
