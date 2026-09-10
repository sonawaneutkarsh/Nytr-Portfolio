"""PostgreSQL 16 proof for the atomic M10B terminal review lifecycle."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.application.target_policy import (
    build_domain_target_set,
    target_policy_payload_sha256,
)
from nutrition_agent.application.target_review import (
    DecideTargetReviewUseCase,
    PersistTargetReviewUseCase,
    TargetReviewConflict,
    TargetReviewNotFound,
    TargetReviewStale,
)
from nutrition_agent.db.sql_repos import (
    SqlGoalPolicyRepository,
    SqlTargetPolicyRepository,
    SqlTargetReviewDecisionRepository,
    SqlTargetReviewRepository,
)
from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    GoalDirection,
    GoalPolicyVersion,
    TargetReviewDecisionValue,
    evaluate_target_review,
    goal_policy_payload_sha256,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; M10B requires scratch Postgres",
)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self, *values: UUID) -> None:
        self.values = list(values)

    def new_id(self) -> UUID:
        return self.values.pop(0)


def _admin():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(DATABASE_URL)  # type: ignore[arg-type]


def _target(user_id: UUID, *, calories: str, created_at: datetime) -> TargetPolicyVersion:
    goals = [
        {"kind": "target", "nutrient": "calories_kcal", "value": calories, "weight": "1.00"},
        {"kind": "floor", "nutrient": "protein_g", "value": "120.00", "weight": "2.00"},
    ]
    return TargetPolicyVersion(
        version_id=uuid4(),
        user_id=user_id,
        policy_version=f"target-{uuid4().hex}",
        goals_jsonb=goals,
        payload_sha256=target_policy_payload_sha256(goals),
        created_at=created_at,
    )


def _seed(user_id: UUID):
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    approved_at = datetime(2026, 8, 14, 12, tzinfo=UTC)
    rate = Decimal("0.25")
    goal = GoalPolicyVersion(
        version_id=uuid4(),
        user_id=user_id,
        policy_version=f"goal-{uuid4().hex}",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=rate,
        payload_sha256=goal_policy_payload_sha256(GoalDirection.GAIN, rate),
        created_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    target = _target(user_id, calories="2500", created_at=approved_at)
    goals = SqlGoalPolicyRepository(DATABASE_URL)
    targets = SqlTargetPolicyRepository(DATABASE_URL)
    reviews = SqlTargetReviewRepository(DATABASE_URL)
    decisions = SqlTargetReviewDecisionRepository(DATABASE_URL)
    goals.save(goal)
    targets.save_approved(target, "initial", approved_at)
    trend = BodyMassTrendSummary(
        as_of_date=date(2026, 8, 28),
        timezone="UTC",
        algorithm_version="body-mass-trend-v1",
        latest_measurement_date=date(2026, 8, 28),
        latest_measurement_age_days=0,
        first_measurement_date=date(2026, 8, 8),
        last_measurement_date=date(2026, 8, 28),
        represented_day_count=7,
        coverage_span_days=20,
        trailing_7d_average_kg=Decimal("70"),
        weekly_rate_kg=Decimal("0"),
        status=BodyMassTrendStatus.READY,
        input_digest=uuid4().hex + uuid4().hex,
    )
    evaluation = evaluate_target_review(
        trend=trend,
        goal_policy=goal,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        current_target_policy=target,
        current_targets=build_domain_target_set(target.goals_jsonb),
        current_target_approved_at=approved_at,
        as_of_date=trend.as_of_date,
    )
    review = (
        PersistTargetReviewUseCase(
            reviews,
            _Clock(datetime(2026, 8, 28, 9, tzinfo=UTC)),
            _Ids(uuid4()),
        )
        .execute(evaluation)
        .review
    )
    return goal, target, review, goals, targets, reviews, decisions


def _use_case(goals, targets, reviews, decisions) -> DecideTargetReviewUseCase:
    return DecideTargetReviewUseCase(
        reviews=reviews,
        goals=goals,
        targets=targets,
        decisions=decisions,
        clock=_Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
        ids=_Ids(uuid4(), uuid4(), uuid4()),
    )


def _counts(user_id: UUID) -> tuple[int, int, int]:
    with _admin() as conn, conn.cursor() as cur:
        target_count = cur.execute(
            "SELECT count(*) FROM target_policy_version WHERE user_id=%s", (str(user_id),)
        ).fetchone()[0]
        log_count = cur.execute(
            "SELECT count(*) FROM decision_log WHERE user_id=%s", (str(user_id),)
        ).fetchone()[0]
        terminal_count = cur.execute(
            "SELECT count(*) FROM target_review_decision WHERE user_id=%s", (str(user_id),)
        ).fetchone()[0]
    return target_count, log_count, terminal_count


@contextmanager
def _failing_insert(table: str):
    from psycopg import sql

    trigger = f"m10b_fail_{table}_{uuid4().hex}"
    function = f"{trigger}_fn"
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'm10b injected failure'; END $$"
            ).format(sql.Identifier(function))
        )
        cur.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION {}()"
            ).format(sql.Identifier(trigger), sql.Identifier(table), sql.Identifier(function))
        )
        conn.commit()
    try:
        yield
    finally:
        with _admin() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP TRIGGER IF EXISTS {} ON {}").format(
                    sql.Identifier(trigger), sql.Identifier(table)
                )
            )
            cur.execute(sql.SQL("DROP FUNCTION IF EXISTS {}()").format(sql.Identifier(function)))
            conn.commit()


def test_approval_commits_all_three_records_exactly_and_replays_original() -> None:
    user = uuid4()
    _, _, review, goals, targets, reviews, decisions = _seed(user)
    use_case = _use_case(goals, targets, reviews, decisions)
    event = uuid4()
    first = use_case.execute(
        user_id=user,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.APPROVED,
        client_event_id=event,
    )
    replay = use_case.execute(
        user_id=user,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.APPROVED,
        client_event_id=event,
    )
    assert first.created is True and replay.created is False
    assert replay.review_decision == first.review_decision
    assert _counts(user) == (2, 2, 1)
    assert first.resulting_policy is not None
    old_non_calorie = [
        goal for goal in review.evaluation.current_goals if goal.nutrient.value != "calories_kcal"
    ]
    new_targets = build_domain_target_set(first.resulting_policy.goals_jsonb)
    assert new_targets.goals["calories_kcal"].value == Decimal("2600")
    assert new_targets.goals["protein_g"].value == old_non_calorie[0].value


def test_rejection_replays_and_terminal_or_event_conflicts_fail() -> None:
    user = uuid4()
    goal, target, review, goals, targets, reviews, decisions = _seed(user)
    use_case = _use_case(goals, targets, reviews, decisions)
    event = uuid4()
    first = use_case.execute(
        user_id=user,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.REJECTED,
        client_event_id=event,
    )
    replay = use_case.execute(
        user_id=user,
        review_id=review.review_id,
        decision=TargetReviewDecisionValue.REJECTED,
        client_event_id=event,
    )
    assert first.created is True and replay.created is False
    assert _counts(user) == (1, 1, 1)
    for decision, retry_event in [
        (TargetReviewDecisionValue.APPROVED, event),
        (TargetReviewDecisionValue.REJECTED, uuid4()),
    ]:
        with pytest.raises(TargetReviewConflict):
            use_case.execute(
                user_id=user,
                review_id=review.review_id,
                decision=decision,
                client_event_id=retry_event,
            )

    second_evaluation = evaluate_target_review(
        trend=replace(review.evaluation.trend, input_digest="b" * 64),
        goal_policy=goal,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        current_target_policy=target,
        current_targets=build_domain_target_set(target.goals_jsonb),
        current_target_approved_at=target.created_at,  # type: ignore[arg-type]
        as_of_date=review.evaluation.as_of_date,
    )
    second_review = (
        PersistTargetReviewUseCase(
            reviews,
            _Clock(datetime(2026, 8, 28, 13, tzinfo=UTC)),
            _Ids(uuid4()),
        )
        .execute(second_evaluation)
        .review
    )
    with pytest.raises(TargetReviewConflict):
        use_case.execute(
            user_id=user,
            review_id=second_review.review_id,
            decision=TargetReviewDecisionValue.REJECTED,
            client_event_id=event,
        )


@pytest.mark.parametrize(
    "table", ["target_policy_version", "decision_log", "target_review_decision"]
)
def test_any_approval_insert_failure_rolls_back_the_entire_transaction(table: str) -> None:
    user = uuid4()
    _, _, review, goals, targets, reviews, decisions = _seed(user)
    use_case = _use_case(goals, targets, reviews, decisions)
    with _failing_insert(table), pytest.raises(Exception, match="m10b injected failure"):
        use_case.execute(
            user_id=user,
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.APPROVED,
            client_event_id=uuid4(),
        )
    assert _counts(user) == (1, 1, 0)


@pytest.mark.parametrize("advance", ["goal", "target"])
def test_newer_goal_or_target_blocks_approval_without_writes(advance: str) -> None:
    user = uuid4()
    _, _, review, goals, targets, reviews, decisions = _seed(user)
    if advance == "goal":
        rate = Decimal("0.30")
        goals.save(
            GoalPolicyVersion(
                version_id=uuid4(),
                user_id=user,
                policy_version=f"goal-{uuid4().hex}",
                direction=GoalDirection.GAIN,
                desired_rate_kg_per_week=rate,
                payload_sha256=goal_policy_payload_sha256(GoalDirection.GAIN, rate),
                created_at=datetime(2026, 8, 27, tzinfo=UTC),
            )
        )
    else:
        newer = _target(user, calories="2550", created_at=datetime(2026, 8, 27, tzinfo=UTC))
        targets.save_approved(newer, "newer", newer.created_at)  # type: ignore[arg-type]
    with pytest.raises(TargetReviewStale):
        _use_case(goals, targets, reviews, decisions).execute(
            user_id=user,
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.APPROVED,
            client_event_id=uuid4(),
        )
    expected = (1, 1, 0) if advance == "goal" else (2, 2, 0)
    assert _counts(user) == expected


def test_cross_owner_decision_is_indistinguishable_from_missing() -> None:
    owner = uuid4()
    _, _, review, goals, targets, reviews, decisions = _seed(owner)
    with pytest.raises(TargetReviewNotFound):
        _use_case(goals, targets, reviews, decisions).execute(
            user_id=uuid4(),
            review_id=review.review_id,
            decision=TargetReviewDecisionValue.REJECTED,
            client_event_id=uuid4(),
        )
    assert _counts(owner) == (1, 1, 0)
