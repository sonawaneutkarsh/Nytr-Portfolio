"""M10B explicit terminal-decision and authoritative review orchestration tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.target_policy import (
    build_domain_target_set,
    target_policy_payload_sha256,
)
from nutrition_agent.application.target_review import (
    CreateGoalPolicyUseCase,
    CreateTargetReviewUseCase,
    DecideTargetReviewUseCase,
    PersistTargetReviewUseCase,
    TargetReviewConflict,
    TargetReviewNotApprovable,
    TargetReviewStale,
    TargetReviewUnavailableReason,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryGoalPolicyRepository,
    InMemoryTargetPolicyRepository,
    InMemoryTargetReviewDecisionRepository,
    InMemoryTargetReviewRepository,
)
from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    GoalDirection,
    TargetReviewDecisionValue,
    evaluate_target_review,
)

USER = UUID("00000000-0000-0000-0000-0000000000a1")
AS_OF = date(2026, 8, 28)
APPROVED_AT = datetime(2026, 8, 14, 12, tzinfo=UTC)
TARGET_ID = UUID(int=201)
EVENT_ID = UUID(int=501)


class _Clock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def now(self) -> datetime:
        return self.values.pop(0)


class _Ids:
    def __init__(self, *values: UUID) -> None:
        self.values = list(values)

    def new_id(self) -> UUID:
        return self.values.pop(0)


class _Trends:
    def __init__(self, result: BodyMassTrendSummary) -> None:
        self.result = result
        self.calls = 0

    def execute(self, *, user_id: UUID, as_of_date: date, timezone: str) -> BodyMassTrendSummary:
        assert user_id == USER and as_of_date == AS_OF and timezone == "UTC"
        self.calls += 1
        return self.result


def _trend(
    *, status: BodyMassTrendStatus = BodyMassTrendStatus.READY, rate: str | None = "0"
) -> BodyMassTrendSummary:
    has_data = status is not BodyMassTrendStatus.NO_DATA
    return BodyMassTrendSummary(
        as_of_date=AS_OF,
        timezone="UTC",
        algorithm_version="body-mass-trend-v1",
        latest_measurement_date=AS_OF if has_data else None,
        latest_measurement_age_days=0 if has_data else None,
        first_measurement_date=AS_OF - timedelta(days=20) if has_data else None,
        last_measurement_date=AS_OF if has_data else None,
        represented_day_count=7 if has_data else 0,
        coverage_span_days=20 if has_data else 0,
        trailing_7d_average_kg=Decimal("70") if has_data else None,
        weekly_rate_kg=Decimal(rate) if rate is not None else None,
        status=status,
        input_digest="a" * 64,
    )


def _target(*, version_id: UUID = TARGET_ID, calories: str = "2500") -> TargetPolicyVersion:
    goals = [
        {"kind": "target", "nutrient": "calories_kcal", "value": calories, "weight": "1.00"},
        {"kind": "floor", "nutrient": "protein_g", "value": "120.00", "weight": "2.00"},
    ]
    return TargetPolicyVersion(
        version_id=version_id,
        user_id=USER,
        policy_version=f"target-{version_id.int}",
        goals_jsonb=goals,
        payload_sha256=target_policy_payload_sha256(goals),
        created_at=APPROVED_AT,
    )


def _seed(*, rate: str = "0"):
    goals = InMemoryGoalPolicyRepository()
    targets = InMemoryTargetPolicyRepository()
    reviews = InMemoryTargetReviewRepository()
    goal = CreateGoalPolicyUseCase(
        goals,
        _Clock(datetime(2026, 8, 1, 12, tzinfo=UTC)),
        _Ids(UUID(int=101)),
    ).execute(
        user_id=USER,
        policy_version="goal-v1",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=Decimal("0.25"),
    )
    target = _target()
    targets.save_approved(target, "initial approval", APPROVED_AT)
    evaluation = evaluate_target_review(
        trend=_trend(rate=rate),
        goal_policy=goal,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        current_target_policy=target,
        current_targets=build_domain_target_set(target.goals_jsonb),
        current_target_approved_at=APPROVED_AT,
        as_of_date=AS_OF,
    )
    review = (
        PersistTargetReviewUseCase(
            reviews,
            _Clock(datetime(2026, 8, 28, 9, tzinfo=UTC)),
            _Ids(UUID(int=301)),
        )
        .execute(evaluation)
        .review
    )
    decisions = InMemoryTargetReviewDecisionRepository(goals, targets, reviews)
    return goals, targets, reviews, decisions, review


def _decider(goals, targets, reviews, decisions, *, event: UUID = EVENT_ID):
    return DecideTargetReviewUseCase(
        reviews=reviews,
        goals=goals,
        targets=targets,
        decisions=decisions,
        clock=_Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
        ids=_Ids(UUID(int=401), UUID(int=402), UUID(int=403)),
    ), event


def test_create_review_reports_missing_goal_or_target_without_trend_or_target_write() -> None:
    trend = _Trends(_trend())
    goals = InMemoryGoalPolicyRepository()
    targets = InMemoryTargetPolicyRepository()
    reviews = InMemoryTargetReviewRepository()

    missing_goal = CreateTargetReviewUseCase(
        trends=trend,  # type: ignore[arg-type]
        goals=goals,
        targets=targets,
        reviews=reviews,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        clock=_Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
        ids=_Ids(UUID(int=301)),
    ).execute(user_id=USER, as_of_date=AS_OF, timezone="UTC")
    assert missing_goal.unavailable_reason is TargetReviewUnavailableReason.NO_GOAL_POLICY
    assert trend.calls == 0

    CreateGoalPolicyUseCase(
        goals,
        _Clock(datetime(2026, 8, 1, 12, tzinfo=UTC)),
        _Ids(UUID(int=101)),
    ).execute(
        user_id=USER,
        policy_version="goal-v1",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=Decimal("0.25"),
    )
    missing_target = CreateTargetReviewUseCase(
        trends=trend,  # type: ignore[arg-type]
        goals=goals,
        targets=targets,
        reviews=reviews,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        clock=_Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
        ids=_Ids(UUID(int=301)),
    ).execute(user_id=USER, as_of_date=AS_OF, timezone="UTC")
    assert missing_target.unavailable_reason is TargetReviewUnavailableReason.NO_TARGET_POLICY
    assert trend.calls == 0 and targets.policies == {}


def test_create_review_uses_authoritative_inputs_replays_and_never_creates_target() -> None:
    goals, targets, reviews, _, _ = _seed()
    empty_reviews = InMemoryTargetReviewRepository()
    trend = _Trends(_trend())
    use_case = CreateTargetReviewUseCase(
        trends=trend,  # type: ignore[arg-type]
        goals=goals,
        targets=targets,
        reviews=empty_reviews,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        clock=_Clock(
            datetime(2026, 8, 28, 10, tzinfo=UTC),
            datetime(2026, 8, 28, 11, tzinfo=UTC),
        ),
        ids=_Ids(UUID(int=310), UUID(int=311)),
    )
    first = use_case.execute(user_id=USER, as_of_date=AS_OF, timezone="UTC")
    replay = use_case.execute(user_id=USER, as_of_date=AS_OF, timezone="UTC")
    assert first.created is True and replay.created is False
    assert replay.review == first.review
    assert trend.calls == 2
    assert len(targets.policies) == 1


def test_approval_is_atomic_shape_preserves_non_calories_and_exact_retry_reuses_original() -> None:
    goals, targets, reviews, decisions, review = _seed()
    use_case, event = _decider(goals, targets, reviews, decisions)
    first = use_case.execute(
        user_id=USER,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.APPROVED,
        client_event_id=event,
    )
    replay = use_case.execute(
        user_id=USER,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.APPROVED,
        client_event_id=event,
    )
    assert first.created is True and replay.created is False
    assert replay.review_decision.decision_id == UUID(int=401)
    assert len(targets.policies) == 2 and len(decisions.outcomes) == 1
    assert len(decisions.decision_logs) == 1
    assert first.resulting_policy is not None
    by_nutrient = {goal["nutrient"]: goal for goal in first.resulting_policy.goals_jsonb}
    assert by_nutrient["calories_kcal"]["value"] == "2600"
    assert by_nutrient["protein_g"] == _target().goals_jsonb[1]


def test_terminal_conflicts_and_rejection_never_create_target_or_decision_log() -> None:
    goals, targets, reviews, decisions, review = _seed()
    use_case, event = _decider(goals, targets, reviews, decisions)
    rejected = use_case.execute(
        user_id=USER,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.REJECTED,
        client_event_id=event,
    )
    replay = use_case.execute(
        user_id=USER,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.REJECTED,
        client_event_id=event,
    )
    assert rejected.created is True and replay.created is False
    assert rejected.resulting_policy is None and len(targets.policies) == 1
    assert decisions.decision_logs == {}
    with pytest.raises(TargetReviewConflict):
        use_case.execute(
            user_id=USER,
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.APPROVED,
            client_event_id=event,
        )
    with pytest.raises(TargetReviewConflict):
        use_case.execute(
            user_id=USER,
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.REJECTED,
            client_event_id=UUID(int=999),
        )


@pytest.mark.parametrize("advance", ["goal", "target"])
def test_approval_fails_when_latest_goal_or_target_advanced(advance: str) -> None:
    goals, targets, reviews, decisions, review = _seed()
    if advance == "goal":
        CreateGoalPolicyUseCase(
            goals,
            _Clock(datetime(2026, 8, 27, 12, tzinfo=UTC)),
            _Ids(UUID(int=102)),
        ).execute(
            user_id=USER,
            policy_version="goal-v2",
            direction=GoalDirection.GAIN,
            desired_rate_kg_per_week=Decimal("0.30"),
        )
    else:
        newer = _target(version_id=UUID(int=202), calories="2550")
        newer = TargetPolicyVersion(
            **{**newer.__dict__, "created_at": datetime(2026, 8, 27, 12, tzinfo=UTC)}
        )
        targets.save_approved(newer, "newer", newer.created_at)  # type: ignore[arg-type]
    use_case, event = _decider(goals, targets, reviews, decisions)
    with pytest.raises(TargetReviewStale):
        use_case.execute(
            user_id=USER,
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.APPROVED,
            client_event_id=event,
        )
    assert len(decisions.outcomes) == 0


def test_non_recommendation_review_cannot_be_approved() -> None:
    goals, targets, reviews, decisions, review = _seed(rate="0.25")
    use_case, event = _decider(goals, targets, reviews, decisions)
    with pytest.raises(TargetReviewNotApprovable):
        use_case.execute(
            user_id=USER,
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.APPROVED,
            client_event_id=event,
        )
    assert len(targets.policies) == 1 and decisions.outcomes == {}
