"""M13B deterministic seven-day nutrition-history tests."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.nutrition_history import (
    GetNutritionHistory7DayUseCase,
    InvalidNutritionHistoryRequest,
)
from nutrition_agent.application.ports import TargetPolicyRepository
from nutrition_agent.db.in_memory_repos import InMemoryTargetPolicyRepository
from nutrition_agent.domain.nutrition.history import (
    CalorieAdherence,
    DailyTargetStatus,
    NutritionHistory7Day,
    ProteinAdherence,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    NutritionAuthority,
    NutritionCompleteness,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion

USER = UUID(int=1)
OTHER = UUID(int=2)
END = date(2026, 9, 7)
ZONE = "America/New_York"


def _policy(index: int, created_at: datetime, *, user_id: UUID = USER) -> TargetPolicyVersion:
    return TargetPolicyVersion(
        version_id=UUID(int=100 + index),
        user_id=user_id,
        policy_version=f"target.v{index}",
        goals_jsonb=[
            {
                "nutrient": "calories_kcal",
                "kind": "target",
                "value": str(2000 + index * 100),
                "weight": "1",
            },
            {
                "nutrient": "protein_g",
                "kind": "floor",
                "value": str(100 + index * 10),
                "weight": "1",
            },
        ],
        payload_sha256=f"{index:064x}",
        created_at=created_at,
    )


def _targets(*policies: TargetPolicyVersion) -> InMemoryTargetPolicyRepository:
    repository = InMemoryTargetPolicyRepository()
    for policy in policies:
        assert policy.created_at is not None
        repository.save_approved(policy, "test", policy.created_at)
    return repository


def _evidence(
    index: int,
    recorded_at: datetime,
    *,
    calories: str | None = "500",
    protein: str | None = "30",
    name: str = "Chicken meal",
) -> ConsumedNutritionEvidence:
    unknown = tuple(
        nutrient
        for nutrient, value in (("calories_kcal", calories), ("protein_g", protein))
        if value is None
    )
    return ConsumedNutritionEvidence(
        entry_id=UUID(int=index),
        recorded_at=recorded_at,
        plan_run_id=UUID(int=1000 + index),
        plan_version_id=UUID(int=2000 + index),
        plan_item_id=UUID(int=3000 + index),
        meal_context="lunch",
        candidate_id=f"candidate-{index}",
        item_name=name,
        configuration_summary=None,
        authority=NutritionAuthority.OFFICIAL,
        confidence="official_published",
        calories_kcal=Decimal(calories) if calories is not None else None,
        protein_g=Decimal(protein) if protein is not None else None,
        unknown_nutrients=unknown,
        provenance_summary="Frozen historical nutrition",
    )


class _EvidenceRepository:
    def __init__(self, entries: tuple[ConsumedNutritionEvidence, ...] = ()) -> None:
        self.entries = entries
        self.calls: list[tuple[UUID, datetime, datetime]] = []

    def list_eaten_evidence(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[ConsumedNutritionEvidence, ...]:
        self.calls.append((user_id, start_inclusive, end_exclusive))
        return tuple(
            item for item in self.entries if start_inclusive <= item.recorded_at < end_exclusive
        )


class _CountingTargets:
    def __init__(self, wrapped: InMemoryTargetPolicyRepository) -> None:
        self.wrapped = wrapped
        self.calls: list[tuple[UUID, datetime, datetime]] = []

    def list_approved_for_window(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[TargetPolicyVersion, ...]:
        self.calls.append((user_id, start_inclusive, end_exclusive))
        return self.wrapped.list_approved_for_window(user_id, start_inclusive, end_exclusive)

    def save_approved(
        self,
        policy: TargetPolicyVersion,
        rationale: str,
        decided_by_clock: datetime,
    ) -> None:
        self.wrapped.save_approved(policy, rationale, decided_by_clock)

    def latest_approved(self, user_id: UUID) -> TargetPolicyVersion | None:
        return self.wrapped.latest_approved(user_id)

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> TargetPolicyVersion | None:
        return self.wrapped.find_by_version_id(user_id, version_id)

    def find_by_version_label(self, user_id: UUID, policy_version: str) -> bool:
        return self.wrapped.find_by_version_label(user_id, policy_version)


def _history(
    targets: TargetPolicyRepository,
    entries: tuple[ConsumedNutritionEvidence, ...] = (),
    *,
    end_date: date = END,
) -> tuple[NutritionHistory7Day, _EvidenceRepository]:
    evidence = _EvidenceRepository(entries)
    result = GetNutritionHistory7DayUseCase(evidence, targets).execute(
        user_id=USER,
        end_date=end_date,
        timezone=ZONE,
    )
    return result, evidence


def test_one_policy_governs_all_days_with_one_bounded_read() -> None:
    targets = _CountingTargets(_targets(_policy(1, datetime(2026, 8, 20, 12, tzinfo=UTC))))
    history, evidence = _history(targets)
    assert all(day.target_status is DailyTargetStatus.AVAILABLE for day in history.days)
    assert all(day.ledger.target.policy_version == "target.v1" for day in history.days)
    assert history.start_date == date(2026, 9, 1)
    assert history.end_date == END
    assert len(evidence.calls) == 1
    assert len(targets.calls) == 1
    assert evidence.calls[0][1:] == (
        datetime(2026, 9, 1, 4, tzinfo=UTC),
        datetime(2026, 9, 8, 4, tzinfo=UTC),
    )


def test_midday_change_invalidates_only_change_day_and_next_day_uses_new_policy() -> None:
    history, _ = _history(
        _targets(
            _policy(1, datetime(2026, 8, 20, tzinfo=UTC)),
            _policy(2, datetime(2026, 9, 3, 19, tzinfo=UTC)),
        ),
        (_evidence(1, datetime(2026, 9, 3, 20, tzinfo=UTC)),),
    )
    changed = history.days[2]
    assert changed.local_date == date(2026, 9, 3)
    assert changed.target_status is DailyTargetStatus.TARGET_CHANGED_DURING_DAY
    assert changed.ledger.target is None
    assert changed.ledger.known_calories_consumed == Decimal("500")
    assert "target_changed_during_day" in changed.reason_codes
    assert history.days[3].ledger.target.policy_version == "target.v2"
    assert history.summary.days_target_changed == 1


def test_first_approval_midday_and_two_changes_same_day_are_change_days() -> None:
    history, _ = _history(
        _targets(
            _policy(1, datetime(2026, 9, 2, 14, tzinfo=UTC)),
            _policy(2, datetime(2026, 9, 4, 14, tzinfo=UTC)),
            _policy(3, datetime(2026, 9, 4, 20, tzinfo=UTC)),
        )
    )
    assert history.days[0].target_status is DailyTargetStatus.UNAVAILABLE
    assert history.days[1].target_status is DailyTargetStatus.TARGET_CHANGED_DURING_DAY
    assert history.days[3].target_status is DailyTargetStatus.TARGET_CHANGED_DURING_DAY
    assert history.days[4].ledger.target.policy_version == "target.v3"


def test_exact_midnight_belongs_to_new_day_and_prior_day_remains_clean() -> None:
    history, _ = _history(
        _targets(
            _policy(1, datetime(2026, 8, 20, tzinfo=UTC)),
            _policy(2, datetime(2026, 9, 4, 4, tzinfo=UTC)),
        )
    )
    assert history.days[2].local_date == date(2026, 9, 3)
    assert history.days[2].target_status is DailyTargetStatus.AVAILABLE
    assert history.days[2].ledger.target.policy_version == "target.v1"
    assert history.days[3].target_status is DailyTargetStatus.AVAILABLE
    assert history.days[3].ledger.target.policy_version == "target.v2"


def test_dst_transition_uses_local_midnights_and_attributes_events_correctly() -> None:
    event = _evidence(1, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    history, evidence = _history(
        _targets(_policy(1, datetime(2026, 10, 1, tzinfo=UTC))),
        (event,),
        end_date=date(2026, 11, 3),
    )
    day = next(value for value in history.days if value.local_date == date(2026, 11, 1))
    assert day.ledger.consumed_item_count == 1
    assert evidence.calls[0][1] == datetime(2026, 10, 28, 4, tzinfo=UTC)
    assert evidence.calls[0][2] == datetime(2026, 11, 4, 5, tzinfo=UTC)


def test_complete_partial_over_target_protein_and_duplicate_events() -> None:
    entries = (
        _evidence(1, datetime(2026, 9, 5, 16, tzinfo=UTC), calories="1200", protein="60"),
        _evidence(2, datetime(2026, 9, 5, 17, tzinfo=UTC), calories="1200", protein="60"),
        _evidence(3, datetime(2026, 9, 6, 16, tzinfo=UTC), calories=None, protein="30"),
    )
    history, _ = _history(_targets(_policy(1, datetime(2026, 8, 20, tzinfo=UTC))), entries)
    complete = history.days[4]
    assert complete.ledger.consumed_item_count == 2
    assert complete.ledger.known_calories_consumed == Decimal("2400")
    assert complete.calorie_adherence is CalorieAdherence.ABOVE_TARGET
    assert complete.protein_adherence is ProteinAdherence.AT_OR_ABOVE_TARGET
    partial = history.days[5]
    assert partial.ledger.nutrition_completeness is NutritionCompleteness.PARTIAL
    assert partial.calorie_adherence is CalorieAdherence.UNAVAILABLE
    assert partial.protein_adherence is ProteinAdherence.BELOW_TARGET
    assert "exact_adherence_unavailable" in partial.reason_codes


def test_exact_calorie_target_and_below_protein_are_factual() -> None:
    history, _ = _history(
        _targets(_policy(1, datetime(2026, 8, 20, tzinfo=UTC))),
        (
            _evidence(
                1,
                datetime(2026, 9, 7, 16, tzinfo=UTC),
                calories="2100",
                protein="100",
            ),
        ),
    )
    day = history.days[-1]
    assert day.calorie_adherence is CalorieAdherence.AT_TARGET
    assert day.protein_adherence is ProteinAdherence.BELOW_TARGET


def test_no_target_no_consumption_and_summary_do_not_invent_missing_values() -> None:
    unknown = _evidence(
        1,
        datetime(2026, 9, 7, 16, tzinfo=UTC),
        calories=None,
        protein=None,
    )
    history, _ = _history(_targets(), (unknown,))
    assert all(day.target_status is DailyTargetStatus.UNAVAILABLE for day in history.days)
    assert history.days[-1].ledger.nutrition_completeness is NutritionCompleteness.UNAVAILABLE
    assert history.summary.days_with_consumption == 1
    assert history.summary.days_unavailable == 7
    assert history.summary.known_calories_total is None


def test_empty_days_never_compare_as_below_target() -> None:
    history, _ = _history(_targets(_policy(1, datetime(2026, 8, 20, tzinfo=UTC))))
    assert all(day.consumed_event_count == 0 for day in history.days)
    assert all(day.calorie_adherence is CalorieAdherence.UNAVAILABLE for day in history.days)
    assert all(day.protein_adherence is ProteinAdherence.UNAVAILABLE for day in history.days)


def test_calorie_and_protein_completeness_are_independent_for_comparison() -> None:
    history, _ = _history(
        _targets(_policy(1, datetime(2026, 8, 20, tzinfo=UTC))),
        (
            _evidence(
                1,
                datetime(2026, 9, 7, 16, tzinfo=UTC),
                calories="2100",
                protein=None,
            ),
        ),
    )
    day = history.days[-1]
    assert day.calorie_adherence is CalorieAdherence.AT_TARGET
    assert day.protein_adherence is ProteinAdherence.UNAVAILABLE


def test_target_history_is_owner_scoped() -> None:
    history, _ = _history(_targets(_policy(1, datetime(2026, 8, 20, tzinfo=UTC), user_id=OTHER)))
    assert history.summary.days_target_available == 0
    assert all(day.ledger.target is None for day in history.days)


def test_invalid_timezone_and_missing_policy_timestamp_fail_closed() -> None:
    with pytest.raises(InvalidNutritionHistoryRequest, match="IANA"):
        GetNutritionHistory7DayUseCase(_EvidenceRepository(), _targets()).execute(
            user_id=USER,
            end_date=END,
            timezone="Mars/Olympus",
        )
    with pytest.raises(InvalidNutritionHistoryRequest):
        GetNutritionHistory7DayUseCase(_EvidenceRepository(), _targets()).execute(
            user_id=USER,
            end_date=date.min,
            timezone=ZONE,
        )

    repository = InMemoryTargetPolicyRepository()
    missing = _policy(1, datetime(2026, 8, 20, tzinfo=UTC))
    repository.policies[missing.version_id] = TargetPolicyVersion(
        version_id=missing.version_id,
        user_id=missing.user_id,
        policy_version=missing.policy_version,
        goals_jsonb=missing.goals_jsonb,
        payload_sha256=missing.payload_sha256,
        created_at=None,
    )
    with pytest.raises(ValueError, match="timestamp"):
        _history(repository)


def test_history_has_no_workout_or_active_energy_input() -> None:
    parameters = inspect.signature(GetNutritionHistory7DayUseCase.execute).parameters
    assert "active_energy_kcal" not in parameters
    assert "workouts" not in parameters
    assert "training" not in parameters
