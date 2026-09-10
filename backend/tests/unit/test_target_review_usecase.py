"""M10A application and in-memory persistence contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.ports import DuplicateTargetReviewError
from nutrition_agent.application.target_review import (
    CreateGoalPolicyUseCase,
    DuplicateGoalPolicyVersion,
    EvaluateTargetReviewUseCase,
    GetLatestGoalPolicyUseCase,
    PersistTargetReviewUseCase,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryGoalPolicyRepository,
    InMemoryTargetReviewRepository,
)
from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.stacks.entities import NutrientKey
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    GoalDirection,
    TargetReviewReason,
    TargetReviewStatus,
)

USER = UUID("00000000-0000-0000-0000-0000000000a1")
AS_OF = date(2026, 8, 28)


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


def _goal(repo: InMemoryGoalPolicyRepository):
    return CreateGoalPolicyUseCase(
        repo,
        _Clock(datetime(2026, 8, 1, 12, tzinfo=UTC)),
        _Ids(UUID(int=101)),
    ).execute(
        user_id=USER,
        policy_version="lean-bulk-v1",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=Decimal("0.25"),
    )


def _evaluation():
    goal_repo = InMemoryGoalPolicyRepository()
    goal = _goal(goal_repo)
    trend = BodyMassTrendSummary(
        as_of_date=AS_OF,
        timezone="UTC",
        algorithm_version="body-mass-trend-v1",
        latest_measurement_date=AS_OF,
        latest_measurement_age_days=0,
        first_measurement_date=AS_OF - timedelta(days=20),
        last_measurement_date=AS_OF,
        represented_day_count=7,
        coverage_span_days=20,
        trailing_7d_average_kg=Decimal("70"),
        weekly_rate_kg=Decimal("0"),
        status=BodyMassTrendStatus.READY,
        input_digest="a" * 64,
    )
    policy = TargetPolicyVersion(
        version_id=UUID(int=201),
        user_id=USER,
        policy_version="target-v1",
        goals_jsonb=[],
        payload_sha256="b" * 64,
        created_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    targets = TargetSet(
        policy_version="target-v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(GoalKind.TARGET, Decimal("2500"), Decimal("1")),
            NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal("120"), Decimal("2")),
        },
    )
    return EvaluateTargetReviewUseCase().execute(
        trend=trend,
        goal_policy=goal,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        current_target_policy=policy,
        current_targets=targets,
        current_target_approved_at=policy.created_at,
        as_of_date=AS_OF,
    )


def test_create_and_get_latest_goal_policy_are_owner_scoped_and_deterministic() -> None:
    repo = InMemoryGoalPolicyRepository()
    first = _goal(repo)
    second = CreateGoalPolicyUseCase(
        repo,
        _Clock(datetime(2026, 8, 2, 12, tzinfo=UTC)),
        _Ids(UUID(int=102)),
    ).execute(
        user_id=USER,
        policy_version="lean-bulk-v2",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=Decimal("0.30"),
    )
    assert GetLatestGoalPolicyUseCase(repo).execute(user_id=USER) == second
    assert repo.find_by_version_id(USER, first.version_id) == first
    assert GetLatestGoalPolicyUseCase(repo).execute(user_id=UUID(int=999)) is None


def test_duplicate_goal_label_or_payload_fails_closed() -> None:
    repo = InMemoryGoalPolicyRepository()
    _goal(repo)
    duplicate_label = CreateGoalPolicyUseCase(
        repo,
        _Clock(datetime(2026, 8, 2, tzinfo=UTC)),
        _Ids(UUID(int=102)),
    )
    with pytest.raises(DuplicateGoalPolicyVersion):
        duplicate_label.execute(
            user_id=USER,
            policy_version="lean-bulk-v1",
            direction=GoalDirection.GAIN,
            desired_rate_kg_per_week=Decimal("0.30"),
        )
    duplicate_payload = CreateGoalPolicyUseCase(
        repo,
        _Clock(datetime(2026, 8, 3, tzinfo=UTC)),
        _Ids(UUID(int=103)),
    )
    with pytest.raises(DuplicateGoalPolicyVersion):
        duplicate_payload.execute(
            user_id=USER,
            policy_version="another-label",
            direction=GoalDirection.GAIN,
            desired_rate_kg_per_week=Decimal("0.25"),
        )


def test_persist_review_first_write_and_exact_replay_return_original_metadata() -> None:
    repo = InMemoryTargetReviewRepository()
    evaluation = _evaluation()
    first_time = datetime(2026, 8, 28, 12, tzinfo=UTC)
    second_time = datetime(2026, 8, 28, 13, tzinfo=UTC)
    use_case = PersistTargetReviewUseCase(
        repo,
        _Clock(first_time, second_time),
        _Ids(UUID(int=301), UUID(int=302)),
    )
    first = use_case.execute(evaluation)
    replay = use_case.execute(evaluation)
    assert first.created is True and replay.created is False
    assert replay.review.review_id == UUID(int=301)
    assert replay.review.created_at == first_time
    assert repo.find_by_digest(USER, evaluation.recommendation_digest) == first.review
    assert len(repo.reviews) == 1


def test_same_review_digest_with_conflicting_evidence_fails_closed() -> None:
    repo = InMemoryTargetReviewRepository()
    evaluation = _evaluation()
    PersistTargetReviewUseCase(
        repo,
        _Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
        _Ids(UUID(int=301)),
    ).execute(evaluation)
    conflicting = replace(
        evaluation,
        reason_codes=(TargetReviewReason.OBSERVED_ABOVE_BAND,),
    )
    with pytest.raises(DuplicateTargetReviewError):
        PersistTargetReviewUseCase(
            repo,
            _Clock(datetime(2026, 8, 28, 13, tzinfo=UTC)),
            _Ids(UUID(int=302)),
        ).execute(conflicting)
    assert len(repo.reviews) == 1


def test_evaluate_use_case_returns_proposal_but_never_creates_target_policy() -> None:
    result = _evaluation()
    assert result.status is TargetReviewStatus.RECOMMENDATION_READY
    assert result.calorie_delta == Decimal("100")
    assert result.prior_target_policy_version_id == UUID(int=201)
    assert not hasattr(EvaluateTargetReviewUseCase(), "apply")
