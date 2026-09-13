from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.manual_foods import (
    AdjustManualFoodUseCase,
    CreateCustomFoodUseCase,
    ListCustomFoodsUseCase,
    RecordManualFoodUseCase,
)
from nutrition_agent.application.ports import DuplicateManualFoodError
from nutrition_agent.db.in_memory_repos import InMemoryCustomFoodRepository
from nutrition_agent.domain.nutrition.custom_foods import ManualMealPeriod, ManualNutritionFacts
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    NutritionAuthority,
    build_daily_nutrition_ledger,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 4, 12, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class Ids:
    def __init__(self) -> None:
        self.value = 1

    def new_id(self) -> UUID:
        value = UUID(int=self.value)
        self.value += 1
        return value


def setup_food() -> tuple[InMemoryCustomFoodRepository, object, Clock, Ids]:
    repo, clock, ids = InMemoryCustomFoodRepository(), Clock(), Ids()
    version = CreateCustomFoodUseCase(repo, clock, ids).execute(
        user_id=UUID(int=100),
        name="Oats shake",
        brand=None,
        serving_description="one shake",
        serving_amount=Decimal("1"),
        serving_unit="serving",
        nutrition=ManualNutritionFacts(calories_kcal=Decimal("500"), protein_g=Decimal("40")),
    )
    return repo, version, clock, ids


def test_half_serving_preserves_unknowns_and_breakfast() -> None:
    repo, version, clock, ids = setup_food()
    outcome = RecordManualFoodUseCase(repo, clock, ids).execute(
        user_id=UUID(int=100),
        food_id=version.food_id,
        food_version_id=version.version_id,
        consumed_amount=Decimal("0.5"),
        consumed_unit="serving",
        meal_period=ManualMealPeriod.BREAKFAST,
        client_event_id=UUID(int=500),
    )
    assert outcome.created
    assert outcome.entry.nutrition.calories_kcal == Decimal("250.0")
    assert outcome.entry.nutrition.protein_g == Decimal("20.0")
    assert outcome.entry.nutrition.fiber_g is None
    assert outcome.entry.meal_period is ManualMealPeriod.BREAKFAST


def test_multiple_servings_scale_exactly() -> None:
    repo, version, clock, ids = setup_food()
    result = RecordManualFoodUseCase(repo, clock, ids).execute(
        user_id=UUID(int=100),
        food_id=version.food_id,
        food_version_id=version.version_id,
        consumed_amount=Decimal("2"),
        consumed_unit="serving",
        meal_period=ManualMealPeriod.LUNCH,
        client_event_id=UUID(int=501),
    )
    assert result.entry.nutrition.calories_kcal == Decimal("1000")


def test_version_edit_does_not_rewrite_historical_consumption() -> None:
    repo, first, clock, ids = setup_food()
    record = RecordManualFoodUseCase(repo, clock, ids)
    old = record.execute(
        user_id=UUID(int=100),
        food_id=first.food_id,
        food_version_id=first.version_id,
        consumed_amount=Decimal("1"),
        consumed_unit="serving",
        meal_period=ManualMealPeriod.BREAKFAST,
        client_event_id=UUID(int=502),
    ).entry
    clock.value = datetime(2026, 9, 5, 12, tzinfo=UTC)
    second = CreateCustomFoodUseCase(repo, clock, ids).execute(
        user_id=UUID(int=100),
        food_id=first.food_id,
        name="Oats shake",
        brand=None,
        serving_description="one shake",
        serving_amount=Decimal("1"),
        serving_unit="serving",
        nutrition=ManualNutritionFacts(calories_kcal=Decimal("550"), protein_g=Decimal("42")),
    )
    assert ListCustomFoodsUseCase(repo).execute(user_id=UUID(int=100)) == (second,)
    assert old.nutrition.calories_kcal == Decimal("500")


def test_idempotent_replay_returns_original_and_conflict_fails() -> None:
    repo, version, clock, ids = setup_food()
    use_case = RecordManualFoodUseCase(repo, clock, ids)
    kwargs = dict(
        user_id=UUID(int=100),
        food_id=version.food_id,
        food_version_id=version.version_id,
        consumed_amount=Decimal("1"),
        consumed_unit="serving",
        meal_period=ManualMealPeriod.DINNER,
        client_event_id=UUID(int=503),
    )
    first = use_case.execute(**kwargs)
    clock.value = datetime(2026, 9, 6, 12, tzinfo=UTC)
    replay = use_case.execute(**kwargs)
    assert not replay.created and replay.entry == first.entry
    with pytest.raises(DuplicateManualFoodError):
        use_case.execute(**{**kwargs, "consumed_amount": Decimal("2")})
    assert repo.consumptions[(UUID(int=100), UUID(int=503))] == first.entry


def test_quantity_correction_and_void_are_append_only_and_idempotent() -> None:
    repo, version, clock, ids = setup_food()
    original = (
        RecordManualFoodUseCase(repo, clock, ids)
        .execute(
            user_id=UUID(int=100),
            food_id=version.food_id,
            food_version_id=version.version_id,
            consumed_amount=Decimal("1"),
            consumed_unit="serving",
            meal_period=ManualMealPeriod.LUNCH,
            client_event_id=UUID(int=510),
        )
        .entry
    )
    adjust = AdjustManualFoodUseCase(repo, clock, ids)
    preview = adjust.preview(
        user_id=UUID(int=100),
        entry_id=original.entry_id,
        amount=Decimal("1.5"),
        unit="serving",
    )
    assert preview[1] == Decimal("1.5")
    assert preview[3].calories_kcal == Decimal("750.0")

    corrected = adjust.correct(
        user_id=UUID(int=100),
        entry_id=original.entry_id,
        amount=Decimal("1.5"),
        unit="serving",
        client_event_id=UUID(int=511),
    )
    assert corrected.created
    assert corrected.replacement is not None
    assert corrected.replacement.nutrition.calories_kcal == Decimal("750.0")
    assert repo.find_active_consumption(UUID(int=100), original.entry_id) is None
    assert (
        repo.find_active_consumption(UUID(int=100), corrected.replacement.entry_id)
        == corrected.replacement
    )

    replay = adjust.correct(
        user_id=UUID(int=100),
        entry_id=original.entry_id,
        amount=Decimal("1.5"),
        unit="serving",
        client_event_id=UUID(int=511),
    )
    assert not replay.created
    assert replay.replacement == corrected.replacement

    removed = adjust.void(
        user_id=UUID(int=100),
        entry_id=corrected.replacement.entry_id,
        client_event_id=UUID(int=512),
    )
    assert removed.created and removed.replacement is None
    assert repo.find_active_consumption(UUID(int=100), corrected.replacement.entry_id) is None
    assert len(repo.consumptions) == 2
    assert len(repo.adjustments) == 2


def test_owner_isolation_and_unit_validation() -> None:
    repo, version, clock, ids = setup_food()
    assert ListCustomFoodsUseCase(repo).execute(user_id=UUID(int=999)) == ()
    with pytest.raises(LookupError):
        RecordManualFoodUseCase(repo, clock, ids).execute(
            user_id=UUID(int=999),
            food_id=version.food_id,
            food_version_id=version.version_id,
            consumed_amount=Decimal("1"),
            consumed_unit="serving",
            meal_period=ManualMealPeriod.BREAKFAST,
            client_event_id=UUID(int=504),
        )
    with pytest.raises(ValueError, match="match"):
        RecordManualFoodUseCase(repo, clock, ids).execute(
            user_id=UUID(int=100),
            food_id=version.food_id,
            food_version_id=version.version_id,
            consumed_amount=Decimal("1"),
            consumed_unit="grams",
            meal_period=ManualMealPeriod.BREAKFAST,
            client_event_id=UUID(int=505),
        )


def test_no_float_or_empty_nutrition() -> None:
    with pytest.raises(TypeError):
        ManualNutritionFacts(calories_kcal=100.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one"):
        ManualNutritionFacts()


def test_plan_manual_and_next_meal_evidence_merge_at_ledger_boundary() -> None:
    common = dict(
        recorded_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
        meal_context="breakfast",
        configuration_summary=None,
        confidence="user_entered",
        unknown_nutrients=(),
        provenance_summary="frozen",
    )
    manual = ConsumedNutritionEvidence(
        entry_id=UUID(int=700),
        plan_run_id=None,
        plan_version_id=None,
        plan_item_id=None,
        candidate_id="manual:700",
        item_name="Eggs",
        authority=NutritionAuthority.USER_ENTERED,
        calories_kcal=Decimal("140"),
        protein_g=Decimal("12"),
        source_system="manual_custom",
        **common,
    )
    plan = ConsumedNutritionEvidence(
        entry_id=UUID(int=701),
        plan_run_id=UUID(int=1),
        plan_version_id=UUID(int=2),
        plan_item_id=UUID(int=3),
        candidate_id="plan",
        item_name="Lunch",
        authority=NutritionAuthority.OFFICIAL,
        calories_kcal=Decimal("600"),
        protein_g=Decimal("40"),
        **{**common, "meal_context": "lunch", "confidence": "official"},
    )
    next_meal = ConsumedNutritionEvidence(
        entry_id=UUID(int=702),
        plan_run_id=None,
        plan_version_id=None,
        plan_item_id=None,
        candidate_id="next-meal",
        item_name="Dinner",
        authority=NutritionAuthority.ESTIMATED,
        calories_kcal=Decimal("450.5"),
        protein_g=Decimal("30.25"),
        source_system="next_meal",
        **{**common, "meal_context": "dinner", "confidence": "estimated"},
    )
    ledger = build_daily_nutrition_ledger(
        local_date=datetime(2026, 9, 4, tzinfo=UTC).date(),
        timezone="UTC",
        consumed_items=(plan, manual, next_meal),
        target=None,
    )
    assert ledger.consumed_item_count == 3
    assert ledger.known_calories_consumed == Decimal("1190.5")
    assert ledger.known_protein_g_consumed == Decimal("82.25")
    assert ledger.authorities == (
        NutritionAuthority.USER_ENTERED,
        NutritionAuthority.OFFICIAL,
        NutritionAuthority.ESTIMATED,
    )
