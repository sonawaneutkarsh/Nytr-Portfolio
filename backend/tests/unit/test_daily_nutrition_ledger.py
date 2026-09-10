"""Deterministic M13A daily nutrition ledger tests."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.daily_nutrition_ledger import (
    GetDailyNutritionLedgerUseCase,
    InvalidDailyNutritionLedgerRequest,
)
from nutrition_agent.db.in_memory_repos import InMemoryTargetPolicyRepository
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    DailyNutritionTarget,
    NutritionAuthority,
    NutritionCompleteness,
    build_daily_nutrition_ledger,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion

USER = UUID(int=1)
DAY = date(2026, 9, 4)
TARGET_ID = UUID(int=90)


def _evidence(
    *,
    entry_id: int,
    recorded_at: datetime,
    calories: str | None = "500",
    protein: str | None = "30",
    authority: NutritionAuthority = NutritionAuthority.OFFICIAL,
    unknown: tuple[str, ...] = (),
) -> ConsumedNutritionEvidence:
    return ConsumedNutritionEvidence(
        entry_id=UUID(int=entry_id),
        recorded_at=recorded_at,
        plan_run_id=UUID(int=10 + entry_id),
        plan_version_id=UUID(int=20 + entry_id),
        plan_item_id=UUID(int=30 + entry_id),
        meal_context="lunch",
        candidate_id=f"candidate-{entry_id}",
        item_name="Chicken meal",
        configuration_summary=None,
        authority=authority,
        confidence="official_published",
        calories_kcal=Decimal(calories) if calories is not None else None,
        protein_g=Decimal(protein) if protein is not None else None,
        unknown_nutrients=unknown,
        provenance_summary="Stacks nutrition frozen with the immutable plan",
    )


def _target() -> DailyNutritionTarget:
    return DailyNutritionTarget(
        policy_version_id=TARGET_ID,
        policy_version="owner-target.v1",
        calories_kcal=Decimal("900"),
        calories_goal_kind="target",
        protein_g=Decimal("50"),
        protein_goal_kind="floor",
    )


def test_ledger_sums_known_values_preserves_negative_remaining_and_provenance() -> None:
    first = _evidence(
        entry_id=1,
        recorded_at=datetime(2026, 9, 4, 16, tzinfo=UTC),
        calories="500.25",
        protein="30.5",
    )
    second = _evidence(
        entry_id=2,
        recorded_at=datetime(2026, 9, 4, 22, tzinfo=UTC),
        calories="500",
        protein="25",
    )

    ledger = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(second, first),
        target=_target(),
    )

    assert ledger.consumed_items == (first, second)
    assert ledger.consumed_item_count == 2
    assert ledger.known_calories_consumed == Decimal("1000.25")
    assert ledger.known_protein_g_consumed == Decimal("55.5")
    assert ledger.remaining_known_calories == Decimal("-100.25")
    assert ledger.remaining_known_protein_g == Decimal("-5.5")
    assert ledger.nutrition_completeness is NutritionCompleteness.COMPLETE
    assert ledger.authorities == (NutritionAuthority.OFFICIAL,)


def test_distinct_consumption_events_for_the_same_food_both_count() -> None:
    first = _evidence(
        entry_id=1,
        recorded_at=datetime(2026, 9, 4, 16, tzinfo=UTC),
        calories="500",
        protein="30",
    )
    second = _evidence(
        entry_id=2,
        recorded_at=datetime(2026, 9, 4, 17, tzinfo=UTC),
        calories="500",
        protein="30",
    )

    ledger = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(first, second),
        target=_target(),
    )

    assert ledger.consumed_item_count == 2
    assert ledger.known_calories_consumed == Decimal("1000")
    assert ledger.known_protein_g_consumed == Decimal("60")


def test_unknown_is_not_zero_and_partial_known_totals_remain_useful() -> None:
    known = _evidence(
        entry_id=1,
        recorded_at=datetime(2026, 9, 4, 16, tzinfo=UTC),
    )
    partial = _evidence(
        entry_id=2,
        recorded_at=datetime(2026, 9, 4, 17, tzinfo=UTC),
        calories=None,
        protein="10",
        authority=NutritionAuthority.PARTIAL,
        unknown=("calories_kcal", "fat_g"),
    )

    ledger = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(known, partial),
        target=_target(),
    )

    assert ledger.known_calories_consumed == Decimal("500")
    assert ledger.known_protein_g_consumed == Decimal("40")
    assert ledger.remaining_known_calories == Decimal("400")
    assert ledger.nutrition_completeness is NutritionCompleteness.PARTIAL
    assert ledger.unknown_nutrients == ("calories_kcal", "fat_g")
    assert "nutrition_partial" in ledger.reason_codes


def test_no_events_is_unavailable_but_recorded_zero_remains_numeric() -> None:
    empty = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(),
        target=None,
    )
    assert empty.known_calories_consumed is None
    assert empty.known_protein_g_consumed is None
    assert empty.nutrition_completeness is NutritionCompleteness.UNAVAILABLE
    assert empty.reason_codes == (
        "no_consumption",
        "nutrition_unavailable",
        "target_unavailable",
    )

    recorded_zero = _evidence(
        entry_id=2,
        recorded_at=datetime(2026, 9, 4, 16, tzinfo=UTC),
        calories="0",
        protein="0",
    )
    zero_ledger = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(recorded_zero,),
        target=None,
    )
    assert zero_ledger.known_calories_consumed == Decimal(0)
    assert zero_ledger.known_protein_g_consumed == Decimal(0)
    assert zero_ledger.nutrition_completeness is NutritionCompleteness.COMPLETE

    unknown = _evidence(
        entry_id=1,
        recorded_at=datetime(2026, 9, 4, 16, tzinfo=UTC),
        calories=None,
        protein=None,
        authority=NutritionAuthority.UNKNOWN,
        unknown=("calories_kcal", "protein_g"),
    )
    unavailable = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(unknown,),
        target=None,
    )
    assert unavailable.known_calories_consumed is None
    assert unavailable.known_protein_g_consumed is None
    assert unavailable.nutrition_completeness is NutritionCompleteness.UNAVAILABLE


class _EvidenceRepository:
    def __init__(self, entries: tuple[ConsumedNutritionEvidence, ...]) -> None:
        self.entries = entries
        self.calls: list[tuple[UUID, datetime, datetime]] = []

    def list_eaten_evidence(
        self, user_id: UUID, start_inclusive: datetime, end_exclusive: datetime
    ) -> tuple[ConsumedNutritionEvidence, ...]:
        self.calls.append((user_id, start_inclusive, end_exclusive))
        return self.entries


def _targets() -> InMemoryTargetPolicyRepository:
    repository = InMemoryTargetPolicyRepository()
    repository.save_approved(
        TargetPolicyVersion(
            version_id=TARGET_ID,
            user_id=USER,
            policy_version="owner-target.v1",
            goals_jsonb=[
                {
                    "nutrient": "calories_kcal",
                    "kind": "target",
                    "value": "900",
                    "weight": "1",
                },
                {
                    "nutrient": "protein_g",
                    "kind": "floor",
                    "value": "50",
                    "weight": "1",
                },
            ],
            payload_sha256="a" * 64,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        rationale="test",
        decided_by_clock=datetime(2026, 9, 1, tzinfo=UTC),
    )
    return repository


def test_use_case_uses_dst_safe_half_open_local_day_and_current_approved_target() -> None:
    entry = _evidence(
        entry_id=1,
        recorded_at=datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
    )
    evidence = _EvidenceRepository((entry,))
    use_case = GetDailyNutritionLedgerUseCase(evidence, _targets())

    result = use_case.execute(
        user_id=USER,
        local_date=date(2026, 11, 1),
        timezone="America/New_York",
    )

    assert evidence.calls == [
        (
            USER,
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
        )
    ]
    assert result.target is not None
    assert result.target.policy_version_id == TARGET_ID
    assert result.target.calories_kcal == Decimal("900")


def test_late_night_local_consumption_is_attributed_to_local_date() -> None:
    late = _evidence(
        entry_id=1,
        recorded_at=datetime(2026, 9, 5, 3, 30, tzinfo=UTC),
    )
    ledger = build_daily_nutrition_ledger(
        local_date=DAY,
        timezone="America/New_York",
        consumed_items=(late,),
        target=_target(),
    )
    assert ledger.consumed_item_count == 1


def test_invalid_timezone_fails_before_repository_read() -> None:
    evidence = _EvidenceRepository(())
    use_case = GetDailyNutritionLedgerUseCase(evidence, _targets())
    with pytest.raises(InvalidDailyNutritionLedgerRequest, match="IANA"):
        use_case.execute(user_id=USER, local_date=DAY, timezone="Mars/Olympus")
    assert evidence.calls == []


def test_workout_energy_cannot_affect_ledger_api_or_math() -> None:
    parameters = inspect.signature(GetDailyNutritionLedgerUseCase.execute).parameters
    assert "active_energy_kcal" not in parameters
    assert "training" not in parameters
    assert "workouts" not in parameters
