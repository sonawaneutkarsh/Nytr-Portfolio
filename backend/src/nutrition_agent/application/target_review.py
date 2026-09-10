"""Application orchestration for the M10A target-review foundation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.ports import (
    Clock,
    GoalPolicyRepository,
    GoalPolicyVersionExistsError,
    IdGenerator,
    StaleTargetReviewError,
    TargetPolicyRepository,
    TargetReviewDecisionConflictError,
    TargetReviewDecisionRepository,
    TargetReviewRepository,
)
from nutrition_agent.application.target_policy import (
    build_domain_target_set,
    target_policy_payload_sha256,
)
from nutrition_agent.domain.health.trend import BodyMassTrendSummary
from nutrition_agent.domain.nutrition.targets import TargetSet
from nutrition_agent.domain.planning.artifacts import DecisionLogEntry, TargetPolicyVersion
from nutrition_agent.domain.stacks.entities import NutrientKey
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    DecideTargetReviewOutcome,
    GoalDirection,
    GoalPolicyVersion,
    PersistTargetReviewOutcome,
    TargetReview,
    TargetReviewDecision,
    TargetReviewDecisionValue,
    TargetReviewEvaluation,
    TargetReviewPolicy,
    TargetReviewStatus,
    evaluate_target_review,
    goal_policy_payload_sha256,
)


class DuplicateGoalPolicyVersion(Exception):
    """The owner already has this goal-policy label or semantic payload."""


class TargetReviewUnavailableReason(StrEnum):
    NO_GOAL_POLICY = "no_goal_policy"
    NO_TARGET_POLICY = "no_target_policy"


@dataclass(frozen=True)
class CreateTargetReviewOutcome:
    review: TargetReview | None
    created: bool
    unavailable_reason: TargetReviewUnavailableReason | None

    def __post_init__(self) -> None:
        if (self.review is None) == (self.unavailable_reason is None):
            raise ValueError("review outcome must be either persisted or unavailable")
        if self.review is None and self.created:
            raise ValueError("unavailable review outcome cannot be created")


class TargetReviewNotFound(Exception):
    """No review exists within the authenticated owner's scope."""


class TargetReviewNotApprovable(Exception):
    """Only a consistent recommendation_ready review can be approved."""


class TargetReviewConflict(Exception):
    """A terminal decision or idempotency key conflicts with persisted state."""


class TargetReviewStale(Exception):
    """Goal or target policy advanced after the review was created."""


class CreateGoalPolicyUseCase:
    def __init__(self, repository: GoalPolicyRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        policy_version: str,
        direction: GoalDirection,
        desired_rate_kg_per_week: Decimal,
    ) -> GoalPolicyVersion:
        if not isinstance(user_id, UUID):
            raise ValueError("user_id must be a UUID")
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ValueError("policy_version is required")
        if len(policy_version) > 200:
            raise ValueError("policy_version too long")
        if not isinstance(direction, GoalDirection):
            raise ValueError("direction must be a GoalDirection")
        version_id = self._ids.new_id()
        created_at = self._clock.now()
        if not isinstance(version_id, UUID):
            raise ValueError("generated goal policy identity must be a UUID")
        if not isinstance(created_at, datetime):
            raise ValueError("clock must return a datetime")
        policy = GoalPolicyVersion(
            version_id=version_id,
            user_id=user_id,
            policy_version=policy_version.strip(),
            direction=direction,
            desired_rate_kg_per_week=desired_rate_kg_per_week,
            payload_sha256=goal_policy_payload_sha256(direction, desired_rate_kg_per_week),
            created_at=created_at,
        )
        try:
            self._repository.save(policy)
        except GoalPolicyVersionExistsError as exc:
            raise DuplicateGoalPolicyVersion(str(exc)) from exc
        return policy


class GetLatestGoalPolicyUseCase:
    def __init__(self, repository: GoalPolicyRepository) -> None:
        self._repository = repository

    def execute(self, *, user_id: UUID) -> GoalPolicyVersion | None:
        if not isinstance(user_id, UUID):
            raise ValueError("user_id must be a UUID")
        return self._repository.latest(user_id)


class EvaluateTargetReviewUseCase:
    """Thin deterministic adapter; all recommendation policy remains domain code."""

    def execute(
        self,
        *,
        trend: BodyMassTrendSummary,
        goal_policy: GoalPolicyVersion,
        review_policy: TargetReviewPolicy,
        current_target_policy: TargetPolicyVersion,
        current_targets: TargetSet,
        current_target_approved_at: datetime,
        as_of_date: date,
    ) -> TargetReviewEvaluation:
        return evaluate_target_review(
            trend=trend,
            goal_policy=goal_policy,
            review_policy=review_policy,
            current_target_policy=current_target_policy,
            current_targets=current_targets,
            current_target_approved_at=current_target_approved_at,
            as_of_date=as_of_date,
        )


class PersistTargetReviewUseCase:
    """Persist an already-deterministic evaluation as immutable audit evidence."""

    def __init__(self, repository: TargetReviewRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def execute(self, evaluation: TargetReviewEvaluation) -> PersistTargetReviewOutcome:
        review_id = self._ids.new_id()
        created_at = self._clock.now()
        if not isinstance(review_id, UUID):
            raise ValueError("generated target review identity must be a UUID")
        if not isinstance(created_at, datetime):
            raise ValueError("clock must return a datetime")
        review = TargetReview(review_id=review_id, evaluation=evaluation, created_at=created_at)
        return self._repository.save(review)


class CreateTargetReviewUseCase:
    """Resolve authoritative current inputs, evaluate, and persist one review."""

    def __init__(
        self,
        *,
        trends: BodyMassTrendUseCase,
        goals: GoalPolicyRepository,
        targets: TargetPolicyRepository,
        reviews: TargetReviewRepository,
        review_policy: TargetReviewPolicy,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._trends = trends
        self._goals = goals
        self._targets = targets
        self._reviews = reviews
        self._review_policy = review_policy
        self._persist = PersistTargetReviewUseCase(reviews, clock, ids)

    def execute(
        self,
        *,
        user_id: UUID,
        as_of_date: date,
        timezone: str,
    ) -> CreateTargetReviewOutcome:
        goal = self._goals.latest(user_id)
        if goal is None:
            return CreateTargetReviewOutcome(
                review=None,
                created=False,
                unavailable_reason=TargetReviewUnavailableReason.NO_GOAL_POLICY,
            )
        target = self._targets.latest_approved(user_id)
        if target is None:
            return CreateTargetReviewOutcome(
                review=None,
                created=False,
                unavailable_reason=TargetReviewUnavailableReason.NO_TARGET_POLICY,
            )
        if target.created_at is None:
            raise ValueError("current target policy lacks an approval timestamp")
        if target_policy_payload_sha256(target.goals_jsonb) != target.payload_sha256:
            raise ValueError("current target policy payload hash is inconsistent")
        trend = self._trends.execute(
            user_id=user_id,
            as_of_date=as_of_date,
            timezone=timezone,
        )
        evaluation = evaluate_target_review(
            trend=trend,
            goal_policy=goal,
            review_policy=self._review_policy,
            current_target_policy=target,
            current_targets=build_domain_target_set(target.goals_jsonb),
            current_target_approved_at=target.created_at,
            as_of_date=as_of_date,
        )
        persisted = self._persist.execute(evaluation)
        return CreateTargetReviewOutcome(
            review=persisted.review,
            created=persisted.created,
            unavailable_reason=None,
        )


def _review_goal_tuples(review: TargetReview, *, proposed: bool) -> tuple[tuple[object, ...], ...]:
    goals = review.evaluation.proposed_goals if proposed else review.evaluation.current_goals
    if goals is None:
        return ()
    return tuple((goal.nutrient, goal.kind, goal.value, goal.weight) for goal in goals)


def _target_goal_tuples(policy: TargetPolicyVersion) -> tuple[tuple[object, ...], ...]:
    targets = build_domain_target_set(policy.goals_jsonb)
    return tuple(
        (nutrient, goal.kind, goal.value, goal.weight)
        for nutrient, goal in sorted(targets.goals.items(), key=lambda item: item[0].value)
    )


def _proposed_target_goals(
    review: TargetReview, current: TargetPolicyVersion
) -> list[dict[str, str]]:
    proposed = review.evaluation.proposed_calorie_target
    if proposed is None:
        raise TargetReviewNotApprovable("review has no proposed calorie target")
    goals = [dict(goal) for goal in current.goals_jsonb]
    calories_seen = 0
    for goal in goals:
        if goal.get("nutrient") == NutrientKey.CALORIES_KCAL.value:
            calories_seen += 1
            goal["value"] = str(proposed)
    if calories_seen != 1:
        raise TargetReviewNotApprovable("current target has no unique calorie goal")
    build_domain_target_set(goals)
    candidate = TargetPolicyVersion(
        version_id=UUID(int=1),
        user_id=current.user_id,
        policy_version="validation-only",
        goals_jsonb=goals,
        payload_sha256=target_policy_payload_sha256(goals),
        created_at=current.created_at,
    )
    if _target_goal_tuples(candidate) != _review_goal_tuples(review, proposed=True):
        raise TargetReviewNotApprovable("stored proposal does not match the target payload")
    return goals


class DecideTargetReviewUseCase:
    """Persist an explicit terminal decision; approval never recomputes review math."""

    def __init__(
        self,
        *,
        reviews: TargetReviewRepository,
        goals: GoalPolicyRepository,
        targets: TargetPolicyRepository,
        decisions: TargetReviewDecisionRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._reviews = reviews
        self._goals = goals
        self._targets = targets
        self._decisions = decisions
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        review_id: UUID,
        decision: TargetReviewDecisionValue,
        client_event_id: UUID,
    ) -> DecideTargetReviewOutcome:
        if not all(isinstance(value, UUID) for value in (user_id, review_id, client_event_id)):
            raise ValueError("decision identities must be UUIDs")
        if not isinstance(decision, TargetReviewDecisionValue):
            raise ValueError("decision must be approved or rejected")

        existing = self._decisions.find_for_review(user_id, review_id)
        if existing is not None:
            stored = existing.review_decision
            if stored.decision is decision and stored.client_event_id == client_event_id:
                return replace(existing, created=False)
            raise TargetReviewConflict("review already has a different terminal decision")

        review = self._reviews.find_by_id(user_id, review_id)
        if review is None:
            raise TargetReviewNotFound("target review not found")

        now = self._clock.now()
        if not isinstance(now, datetime):
            raise ValueError("clock must return a datetime")
        decision_id = self._ids.new_id()
        if not isinstance(decision_id, UUID):
            raise ValueError("generated review decision identity must be a UUID")
        rationale = f"User {decision.value} immutable target review {review.review_id}"
        resulting_policy: TargetPolicyVersion | None = None
        target_decision_log: DecisionLogEntry | None = None
        resulting_policy_id: UUID | None = None

        if decision is TargetReviewDecisionValue.APPROVED:
            if review.evaluation.review_policy != M10A_TARGET_REVIEW_POLICY:
                raise TargetReviewNotApprovable("review policy is not supported for approval")
            if review.evaluation.status is not TargetReviewStatus.RECOMMENDATION_READY:
                raise TargetReviewNotApprovable("review is not recommendation_ready")
            latest_goal = self._goals.latest(user_id)
            latest_target = self._targets.latest_approved(user_id)
            if latest_goal is None or latest_target is None:
                raise TargetReviewStale("review references are no longer current")
            if latest_goal.version_id != review.evaluation.goal_policy.version_id:
                raise TargetReviewStale("latest goal policy changed after review")
            if latest_target.version_id != review.evaluation.prior_target_policy_version_id:
                raise TargetReviewStale("latest target policy changed after review")
            if (
                target_policy_payload_sha256(latest_target.goals_jsonb)
                != latest_target.payload_sha256
            ):
                raise TargetReviewNotApprovable("current target payload hash is inconsistent")
            if _target_goal_tuples(latest_target) != _review_goal_tuples(review, proposed=False):
                raise TargetReviewNotApprovable("review current-target evidence is inconsistent")
            goals = _proposed_target_goals(review, latest_target)
            resulting_policy_id = self._ids.new_id()
            if not isinstance(resulting_policy_id, UUID):
                raise ValueError("generated target policy identity must be a UUID")
            resulting_policy = TargetPolicyVersion(
                version_id=resulting_policy_id,
                user_id=user_id,
                policy_version=f"target-review-{review.recommendation_digest[:24]}",
                goals_jsonb=goals,
                payload_sha256=target_policy_payload_sha256(goals),
                created_at=now,
            )
            target_decision_log_id = self._ids.new_id()
            if not isinstance(target_decision_log_id, UUID):
                raise ValueError("generated target decision-log identity must be a UUID")
            target_decision_log = DecisionLogEntry(
                decision_id=target_decision_log_id,
                user_id=user_id,
                subject="target_policy",
                decision="approved",
                rationale=rationale,
                policy_version_id=resulting_policy_id,
                decided_at=now,
            )

        terminal = TargetReviewDecision(
            decision_id=decision_id,
            user_id=user_id,
            review_id=review_id,
            decision=decision,
            rationale=rationale,
            resulting_target_policy_version_id=resulting_policy_id,
            client_event_id=client_event_id,
            decided_at=now,
        )
        try:
            return self._decisions.decide(
                review,
                terminal,
                resulting_policy,
                target_decision_log,
            )
        except TargetReviewDecisionConflictError as exc:
            raise TargetReviewConflict(str(exc)) from exc
        except StaleTargetReviewError as exc:
            raise TargetReviewStale(str(exc)) from exc


__all__ = [
    "CreateTargetReviewOutcome",
    "CreateTargetReviewUseCase",
    "CreateGoalPolicyUseCase",
    "DuplicateGoalPolicyVersion",
    "DecideTargetReviewUseCase",
    "EvaluateTargetReviewUseCase",
    "GetLatestGoalPolicyUseCase",
    "PersistTargetReviewUseCase",
    "TargetReviewConflict",
    "TargetReviewNotApprovable",
    "TargetReviewNotFound",
    "TargetReviewStale",
    "TargetReviewUnavailableReason",
]
