"""M18 evidence-bounded longitudinal progress tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.daily_nutrition_ledger import absolute_day_bounds
from nutrition_agent.application.progress import GetLongitudinalProgressUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryGoalPolicyRepository,
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.health.entities import BodyMassSample, SampleDeletion, SyncBatch
from nutrition_agent.domain.health.trend import BodyMassObservation, BodyMassTrendStatus
from nutrition_agent.domain.nutrition.history import (
    CalorieAdherence,
    DailyTargetStatus,
    ProteinAdherence,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    DailyNutritionTarget,
    NutritionAuthority,
    build_daily_nutrition_ledger,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.progress import (
    LOGGING_COVERAGE_LIMITATION,
    NO_CAUSALITY_LIMITATION,
    PROGRESS_POLICY_VERSION,
    RECORD_TIME_LIMITATION,
    ProgressNutritionDay,
    RecordedNutrientState,
    build_progress_nutrition_day,
    summarize_progress_nutrition,
)
from nutrition_agent.domain.target_review import (
    GoalBandStatus,
    GoalDirection,
    GoalPolicyVersion,
    goal_policy_payload_sha256,
)

USER = UUID(int=1)
AS_OF = date(2026, 11, 3)
ZONE = "America/New_York"


def _target(index: int = 1) -> DailyNutritionTarget:
    return DailyNutritionTarget(
        policy_version_id=UUID(int=100 + index),
        policy_version=f"target.v{index}",
        calories_kcal=Decimal(str(1900 + index * 100)),
        calories_goal_kind="target",
        protein_g=Decimal(str(90 + index * 10)),
        protein_goal_kind="floor",
    )


def _item(
    index: int,
    recorded_at: datetime,
    *,
    calories: str | None = "500",
    protein: str | None = "30",
    authority: NutritionAuthority = NutritionAuthority.OFFICIAL,
    source: str = "plan",
) -> ConsumedNutritionEvidence:
    return ConsumedNutritionEvidence(
        entry_id=UUID(int=index),
        recorded_at=recorded_at,
        plan_run_id=UUID(int=1000 + index) if source == "plan" else None,
        plan_version_id=UUID(int=2000 + index) if source == "plan" else None,
        plan_item_id=UUID(int=3000 + index) if source == "plan" else None,
        meal_context="lunch",
        candidate_id=f"candidate-{index}",
        item_name=f"Food {index}",
        configuration_summary=None,
        authority=authority,
        confidence=authority.value,
        calories_kcal=Decimal(calories) if calories is not None else None,
        protein_g=Decimal(protein) if protein is not None else None,
        unknown_nutrients=tuple(
            name
            for name, value in (("calories_kcal", calories), ("protein_g", protein))
            if value is None
        ),
        provenance_summary="Frozen evidence",
        source_system=source,
    )


def _progress_day(
    local_date: date,
    items: tuple[ConsumedNutritionEvidence, ...],
    *,
    target_status: DailyTargetStatus = DailyTargetStatus.AVAILABLE,
) -> ProgressNutritionDay:
    target = _target() if target_status is DailyTargetStatus.AVAILABLE else None
    ledger = build_daily_nutrition_ledger(
        local_date=local_date,
        timezone="UTC",
        consumed_items=items,
        target=target,
    )
    return build_progress_nutrition_day(ledger, target_status)


def test_no_record_day_is_unavailable_while_recorded_zero_is_quantified() -> None:
    empty = _progress_day(date(2026, 9, 1), ())
    assert empty.recorded_calories_state is RecordedNutrientState.NO_RECORDS
    assert empty.recorded_calories_kcal is None
    assert empty.recorded_protein_state is RecordedNutrientState.NO_RECORDS
    assert empty.recorded_protein_g is None
    assert empty.calorie_target_comparison is CalorieAdherence.UNAVAILABLE
    assert empty.protein_target_comparison is ProteinAdherence.UNAVAILABLE

    zero = _progress_day(
        date(2026, 9, 2),
        (_item(1, datetime(2026, 9, 2, 12, tzinfo=UTC), calories="0", protein="0"),),
    )
    assert zero.recorded_calories_state is RecordedNutrientState.QUANTIFIED
    assert zero.recorded_calories_kcal == Decimal(0)
    assert zero.recorded_protein_state is RecordedNutrientState.QUANTIFIED
    assert zero.recorded_protein_g == Decimal(0)
    assert zero.calorie_target_comparison is CalorieAdherence.BELOW_TARGET
    assert zero.protein_target_comparison is ProteinAdherence.BELOW_TARGET


def test_calorie_and_protein_evidence_are_classified_independently() -> None:
    day = _progress_day(
        date(2026, 9, 2),
        (
            _item(1, datetime(2026, 9, 2, 12, tzinfo=UTC), calories="500", protein="20"),
            _item(2, datetime(2026, 9, 2, 18, tzinfo=UTC), calories="400", protein=None),
        ),
    )
    assert day.recorded_calories_state is RecordedNutrientState.QUANTIFIED
    assert day.recorded_calories_kcal == Decimal("900")
    assert day.recorded_protein_state is RecordedNutrientState.PARTIAL
    assert day.recorded_protein_g == Decimal("20")
    assert day.calorie_target_comparison is CalorieAdherence.BELOW_TARGET
    assert day.protein_target_comparison is ProteinAdherence.UNAVAILABLE


def test_summaries_use_only_quantified_recorded_days_and_retain_provenance() -> None:
    start = date(2026, 9, 1)
    days = (
        _progress_day(start, ()),
        _progress_day(
            start + timedelta(days=1),
            (_item(1, datetime(2026, 9, 2, 12, tzinfo=UTC), calories="0", protein="0"),),
        ),
        _progress_day(
            start + timedelta(days=2),
            (
                _item(
                    2,
                    datetime(2026, 9, 3, 12, tzinfo=UTC),
                    calories="600",
                    protein=None,
                    authority=NutritionAuthority.ESTIMATED,
                    source="next_meal",
                ),
            ),
        ),
        _progress_day(
            start + timedelta(days=3),
            (
                _item(
                    3,
                    datetime(2026, 9, 4, 12, tzinfo=UTC),
                    calories="1200",
                    protein="120",
                    authority=NutritionAuthority.USER_ENTERED,
                    source="manual_custom",
                ),
            ),
        ),
    )
    summary = summarize_progress_nutrition(days)
    assert summary.days_with_recorded_events == 3
    assert summary.days_without_recorded_events == 1
    assert summary.calories.quantified_recorded_days == 3
    assert summary.calories.average_recorded_value == Decimal("600")
    assert summary.calories.average_denominator_days == 3
    assert summary.protein.quantified_recorded_days == 2
    assert summary.protein.unavailable_recorded_days == 1
    assert summary.protein.average_recorded_value == Decimal("60")
    assert summary.includes_estimates
    assert dict((item.key, item.count) for item in summary.source_event_counts) == {
        "manual_custom": 1,
        "next_meal": 1,
        "plan": 1,
    }
    assert summary.calorie_target_comparisons.eligible_day_count == 3
    assert summary.calorie_target_comparisons.unavailable == 1
    assert summary.protein_target_comparisons.eligible_day_count == 2
    assert summary.protein_target_comparisons.unavailable == 2


class _BodyRepository:
    def __init__(self, observations: tuple[BodyMassObservation, ...]) -> None:
        self.observations = observations
        self.calls: list[tuple[UUID, datetime, datetime]] = []

    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[BodyMassObservation, ...]:
        self.calls.append((user_id, start_inclusive, end_exclusive))
        return tuple(
            item
            for item in self.observations
            if start_inclusive <= item.measured_at < end_exclusive
        )


class _EvidenceRepository:
    def __init__(self, entries: tuple[ConsumedNutritionEvidence, ...]) -> None:
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


def _policy(index: int, created_at: datetime) -> TargetPolicyVersion:
    return TargetPolicyVersion(
        version_id=UUID(int=100 + index),
        user_id=USER,
        policy_version=f"target.v{index}",
        goals_jsonb=[
            {
                "nutrient": "calories_kcal",
                "kind": "target",
                "value": str(1900 + index * 100),
                "weight": "1",
            },
            {
                "nutrient": "protein_g",
                "kind": "floor",
                "value": str(90 + index * 10),
                "weight": "1",
            },
        ],
        payload_sha256=f"{index:064x}",
        created_at=created_at,
    )


def _goal() -> GoalPolicyVersion:
    direction = GoalDirection.GAIN
    rate = Decimal("0.20")
    return GoalPolicyVersion(
        version_id=UUID(int=500),
        user_id=USER,
        policy_version="goal.v1",
        direction=direction,
        desired_rate_kg_per_week=rate,
        payload_sha256=goal_policy_payload_sha256(direction, rate),
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_progress_use_case_uses_fixed_windows_shared_m9_and_target_history() -> None:
    observations = tuple(
        BodyMassObservation(
            sample_uuid=UUID(int=index + 1),
            value_kg=Decimal("70"),
            measured_at=datetime.combine(
                AS_OF + timedelta(days=offset),
                datetime.min.time(),
                tzinfo=UTC,
            ),
        )
        for index, offset in enumerate((-89, -20, -18, -16, -14, -12, -6, 0, 1))
    ) + (
        BodyMassObservation(
            sample_uuid=UUID(int=99),
            value_kg=Decimal("72"),
            measured_at=datetime(2026, 11, 3, 12, tzinfo=UTC),
        ),
    )
    body = _BodyRepository(observations)
    entries = (_item(1, datetime(2026, 11, 3, 17, tzinfo=UTC), calories="500", protein=None),)
    evidence = _EvidenceRepository(entries)
    targets = InMemoryTargetPolicyRepository()
    policies = (
        _policy(1, datetime(2026, 9, 1, tzinfo=UTC)),
        _policy(2, datetime(2026, 10, 25, 4, tzinfo=UTC)),
        _policy(3, datetime(2026, 10, 27, 16, tzinfo=UTC)),
    )
    for policy in policies:
        assert policy.created_at is not None
        targets.save_approved(policy, "test", policy.created_at)
    goals = InMemoryGoalPolicyRepository()
    goals.save(_goal())

    result = GetLongitudinalProgressUseCase(
        body_mass=body,
        consumption=evidence,
        targets=targets,
        goals=goals,
    ).execute(user_id=USER, as_of_date=AS_OF, timezone=ZONE)

    assert result.policy_version == PROGRESS_POLICY_VERSION
    assert result.body_window_start_date == date(2026, 8, 6)
    assert result.nutrition_window_start_date == date(2026, 10, 7)
    assert body.calls[0][1:] == (
        datetime(2026, 8, 6, 4, tzinfo=UTC),
        datetime(2026, 11, 4, 5, tzinfo=UTC),
    )
    assert evidence.calls[0][1:] == (
        datetime(2026, 10, 7, 4, tzinfo=UTC),
        datetime(2026, 11, 4, 5, tzinfo=UTC),
    )
    assert len(result.body_daily_medians) == 8
    assert result.body_daily_medians[-1].median_kg == Decimal("71")
    assert result.body_daily_medians[-1].observation_count == 2
    assert result.body_trend_28d.status is BodyMassTrendStatus.READY
    assert result.goal_interpretation is not None
    assert result.goal_interpretation.status is GoalBandStatus.BELOW_BAND
    assert tuple(change.target_policy_version for change in result.target_changes) == (
        "target.v2",
        "target.v3",
    )
    exact_midnight = next(
        day for day in result.nutrition_days if day.local_date == date(2026, 10, 25)
    )
    assert exact_midnight.target is not None
    assert exact_midnight.target.policy_version == "target.v2"
    intraday = next(day for day in result.nutrition_days if day.local_date == date(2026, 10, 27))
    assert intraday.target_status is DailyTargetStatus.TARGET_CHANGED_DURING_DAY
    assert result.nutrition_days[-1].recorded_calories_state is RecordedNutrientState.QUANTIFIED
    assert result.nutrition_days[-1].recorded_protein_state is RecordedNutrientState.UNAVAILABLE
    assert result.nutrition_summary_7d.window_days == 7
    assert result.nutrition_summary_28d.window_days == 28
    assert result.limitation_codes == (
        LOGGING_COVERAGE_LIMITATION,
        RECORD_TIME_LIMITATION,
        NO_CAUSALITY_LIMITATION,
    )


def test_sparse_progress_without_goal_or_target_fails_closed() -> None:
    body = _BodyRepository(
        (
            BodyMassObservation(
                sample_uuid=UUID(int=1),
                value_kg=Decimal("70"),
                measured_at=datetime(2026, 11, 3, 12, tzinfo=UTC),
            ),
        )
    )
    result = GetLongitudinalProgressUseCase(
        body_mass=body,
        consumption=_EvidenceRepository(()),
        targets=InMemoryTargetPolicyRepository(),
        goals=InMemoryGoalPolicyRepository(),
    ).execute(user_id=USER, as_of_date=AS_OF, timezone=ZONE)
    assert result.body_trend_28d.status is BodyMassTrendStatus.INSUFFICIENT
    assert result.current_goal is None
    assert result.goal_interpretation is None
    assert result.target_changes == ()
    assert result.nutrition_summary_28d.days_with_recorded_events == 0
    assert result.nutrition_summary_28d.calories.average_recorded_value is None
    assert result.nutrition_summary_28d.calorie_target_comparisons.eligible_day_count == 0


def test_progress_reads_only_active_bounded_body_mass_evidence() -> None:
    body = InMemoryHealthBodyMassRepository()
    body.apply_batch(
        USER,
        SyncBatch(
            client_batch_id=UUID(int=700),
            added=(
                BodyMassSample(
                    sample_uuid=UUID(int=701),
                    value_kg=Decimal("70"),
                    sample_start=datetime(2026, 11, 1, 12, tzinfo=UTC),
                    sample_end=datetime(2026, 11, 1, 12, tzinfo=UTC),
                ),
                BodyMassSample(
                    sample_uuid=UUID(int=702),
                    value_kg=Decimal("80"),
                    sample_start=datetime(2026, 11, 2, 12, tzinfo=UTC),
                    sample_end=datetime(2026, 11, 2, 12, tzinfo=UTC),
                ),
                BodyMassSample(
                    sample_uuid=UUID(int=703),
                    value_kg=Decimal("90"),
                    sample_start=datetime(2026, 11, 4, 12, tzinfo=UTC),
                    sample_end=datetime(2026, 11, 4, 12, tzinfo=UTC),
                ),
            ),
            deletions=(),
        ),
    )
    body.apply_batch(
        USER,
        SyncBatch(
            client_batch_id=UUID(int=704),
            added=(),
            deletions=(SampleDeletion(UUID(int=702)),),
        ),
    )

    result = GetLongitudinalProgressUseCase(
        body_mass=body,
        consumption=_EvidenceRepository(()),
        targets=InMemoryTargetPolicyRepository(),
        goals=InMemoryGoalPolicyRepository(),
    ).execute(user_id=USER, as_of_date=AS_OF, timezone=ZONE)

    assert tuple((point.local_date, point.median_kg) for point in result.body_daily_medians) == (
        (date(2026, 11, 1), Decimal("70")),
    )


def test_progress_bounds_helpers_remain_dst_safe() -> None:
    start, end = absolute_day_bounds(date(2026, 11, 1), ZONE)
    assert start == datetime(2026, 11, 1, 4, tzinfo=UTC)
    assert end == datetime(2026, 11, 2, 5, tzinfo=UTC)
