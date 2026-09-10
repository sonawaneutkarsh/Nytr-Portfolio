"""Authenticated M10B goal/review/terminal-decision API contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.target_policy import target_policy_payload_sha256
from nutrition_agent.application.target_review import (
    CreateGoalPolicyUseCase,
    CreateTargetReviewUseCase,
    DecideTargetReviewUseCase,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryGoalPolicyRepository,
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
    InMemoryTargetReviewDecisionRepository,
    InMemoryTargetReviewRepository,
)
from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    GoalDirection,
    TargetReviewPolicy,
)

SECRET = "m10b-api-test-secret-for-hs256-long-enough"
AUDIENCE = "authenticated"
USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
USER_B = UUID("00000000-0000-0000-0000-0000000000b2")
AS_OF = date(2026, 8, 28)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self, start: int) -> None:
        self.value = start

    def new_id(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


class _Trends:
    def __init__(self, result: BodyMassTrendSummary) -> None:
        self.result = result

    def execute(self, *, user_id: UUID, as_of_date: date, timezone: str) -> BodyMassTrendSummary:
        del user_id
        assert as_of_date == AS_OF and timezone == "UTC"
        return self.result


def _settings() -> HealthApiSettings:
    return HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )


def _token(user_id: UUID = USER_A) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _headers(user_id: UUID = USER_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def _trend(
    *, status: BodyMassTrendStatus = BodyMassTrendStatus.READY, rate: str | None = "0"
) -> BodyMassTrendSummary:
    has_data = status is not BodyMassTrendStatus.NO_DATA
    latest = AS_OF if status is not BodyMassTrendStatus.STALE else AS_OF - timedelta(days=8)
    return BodyMassTrendSummary(
        as_of_date=AS_OF,
        timezone="UTC",
        algorithm_version="body-mass-trend-v1",
        latest_measurement_date=latest if has_data else None,
        latest_measurement_age_days=(AS_OF - latest).days if has_data else None,
        first_measurement_date=AS_OF - timedelta(days=20) if has_data else None,
        last_measurement_date=latest if has_data else None,
        represented_day_count=7 if has_data else 0,
        coverage_span_days=20 if has_data else 0,
        trailing_7d_average_kg=Decimal("70") if has_data else None,
        weekly_rate_kg=Decimal(rate) if rate is not None else None,
        status=status,
        input_digest="a" * 64,
    )


def _target(*, version_id: UUID, approved_at: datetime, calories: str = "2500"):
    goals = [
        {"kind": "target", "nutrient": "calories_kcal", "value": calories, "weight": "1"},
        {"kind": "floor", "nutrient": "protein_g", "value": "120", "weight": "2"},
    ]
    return TargetPolicyVersion(
        version_id=version_id,
        user_id=USER_A,
        policy_version=f"target-{version_id.int}",
        goals_jsonb=goals,
        payload_sha256=target_policy_payload_sha256(goals),
        created_at=approved_at,
    )


def _app(
    *,
    trend: BodyMassTrendSummary | None = None,
    seed_goal: bool = True,
    seed_target: bool = True,
    approved_at: datetime = datetime(2026, 8, 14, 12, tzinfo=UTC),
    review_policy: TargetReviewPolicy = M10A_TARGET_REVIEW_POLICY,
):
    settings = _settings()
    health = HealthBodyMassSyncUseCase(
        HealthSyncDeps(
            InMemoryHealthBodyMassRepository(), _Clock(datetime(2026, 8, 28, 12, tzinfo=UTC))
        )
    )
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    goals = InMemoryGoalPolicyRepository()
    targets = InMemoryTargetPolicyRepository()
    reviews = InMemoryTargetReviewRepository()
    decisions = InMemoryTargetReviewDecisionRepository(goals, targets, reviews)
    if seed_goal:
        CreateGoalPolicyUseCase(
            goals,
            _Clock(datetime(2026, 8, 1, 12, tzinfo=UTC)),
            _Ids(100),
        ).execute(
            user_id=USER_A,
            policy_version="goal-v1",
            direction=GoalDirection.GAIN,
            desired_rate_kg_per_week=Decimal("0.25"),
        )
    if seed_target:
        target = _target(version_id=UUID(int=201), approved_at=approved_at)
        targets.save_approved(target, "initial", approved_at)
    app.state.target_review_goal_repository = goals
    app.state.target_review_create_goal_use_case = CreateGoalPolicyUseCase(
        goals,
        _Clock(datetime(2026, 8, 28, 8, tzinfo=UTC)),
        _Ids(600),
    )
    app.state.target_review_create_use_case = CreateTargetReviewUseCase(
        trends=_Trends(trend or _trend()),  # type: ignore[arg-type]
        goals=goals,
        targets=targets,
        reviews=reviews,
        review_policy=review_policy,
        clock=_Clock(datetime(2026, 8, 28, 9, tzinfo=UTC)),
        ids=_Ids(300),
    )
    app.state.target_review_decide_use_case = DecideTargetReviewUseCase(
        reviews=reviews,
        goals=goals,
        targets=targets,
        decisions=decisions,
        clock=_Clock(datetime(2026, 8, 28, 10, tzinfo=UTC)),
        ids=_Ids(400),
    )
    return TestClient(app), goals, targets, reviews, decisions


def _review(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/target-reviews",
        headers=_headers(),
        json={"as_of_date": AS_OF.isoformat(), "timezone": "UTC"},
    )
    assert response.status_code == 201
    return response.json()


def test_goal_policy_create_latest_auth_validation_and_owner_scope() -> None:
    client, _, _, _, _ = _app(seed_goal=False, seed_target=False)
    assert client.get("/v1/goal-policies/latest").status_code == 401
    created = client.post(
        "/v1/goal-policies",
        headers=_headers(),
        json={
            "policy_version": "maintain-v1",
            "direction": "maintain",
            "desired_rate_kg_per_week": "0",
        },
    )
    assert created.status_code == 201
    assert created.json()["direction"] == "maintain"
    assert client.get("/v1/goal-policies/latest", headers=_headers()).json() == created.json()
    assert client.get("/v1/goal-policies/latest", headers=_headers(USER_B)).json() is None
    invalid = client.post(
        "/v1/goal-policies",
        headers=_headers(),
        json={
            "policy_version": "bad",
            "direction": "gain",
            "desired_rate_kg_per_week": "-0.1",
        },
    )
    assert invalid.status_code == 422
    assert (
        client.post(
            "/v1/goal-policies",
            headers=_headers(),
            json={
                "policy_version": "hidden-extra",
                "direction": "maintain",
                "desired_rate_kg_per_week": "0",
                "automatic": True,
            },
        ).status_code
        == 422
    )


def test_review_returns_typed_no_goal_and_no_target_states() -> None:
    no_goal, *_ = _app(seed_goal=False, seed_target=False)
    no_target, *_ = _app(seed_goal=True, seed_target=False)
    payload = {"as_of_date": AS_OF.isoformat(), "timezone": "UTC"}
    assert no_goal.post("/v1/target-reviews", headers=_headers(), json=payload).json() == {
        "state": "no_goal_policy"
    }
    assert no_target.post("/v1/target-reviews", headers=_headers(), json=payload).json() == {
        "state": "no_target_policy"
    }
    assert (
        no_target.post(
            "/v1/target-reviews",
            headers=_headers(),
            json={**payload, "observed_weekly_rate": "9.9"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("status", "rate", "approved_at", "policy", "expected"),
    [
        (
            BodyMassTrendStatus.NO_DATA,
            None,
            datetime(2026, 8, 14, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "evidence_unavailable",
        ),
        (
            BodyMassTrendStatus.INSUFFICIENT,
            None,
            datetime(2026, 8, 14, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "evidence_unavailable",
        ),
        (
            BodyMassTrendStatus.STALE,
            None,
            datetime(2026, 8, 14, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "evidence_unavailable",
        ),
        (
            BodyMassTrendStatus.READY,
            "0.25",
            datetime(2026, 8, 14, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "within_band",
        ),
        (
            BodyMassTrendStatus.READY,
            "0",
            datetime(2026, 8, 20, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "cooldown_hold",
        ),
        (
            BodyMassTrendStatus.READY,
            "0",
            datetime(2026, 8, 14, tzinfo=UTC),
            replace(M10A_TARGET_REVIEW_POLICY, upper_calorie_bound=Decimal("2500")),
            "bound_hold",
        ),
        (
            BodyMassTrendStatus.READY,
            "0",
            datetime(2026, 8, 14, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "recommendation_ready",
        ),
        (
            BodyMassTrendStatus.READY,
            "0.5",
            datetime(2026, 8, 14, tzinfo=UTC),
            M10A_TARGET_REVIEW_POLICY,
            "recommendation_ready",
        ),
    ],
)
def test_review_generation_exposes_all_states_without_target_write(
    status, rate, approved_at, policy, expected
) -> None:
    client, _, targets, _, _ = _app(
        trend=_trend(status=status, rate=rate),
        approved_at=approved_at,
        review_policy=policy,
    )
    result = _review(client)
    assert result["status"] == expected
    assert result["trend"]["status"] == status.value  # type: ignore[index]
    assert len(targets.policies) == 1
    if expected == "recommendation_ready":
        expected_delta = "100" if Decimal(rate) < Decimal("0.15") else "-100"
        assert result["target_policy"]["calorie_delta"] == expected_delta  # type: ignore[index]


def test_decision_approval_replay_conflict_no_arbitrary_target_and_cross_owner_404() -> None:
    client, _, targets, _, decisions = _app(trend=_trend(rate="0"))
    review = _review(client)
    review_id = review["review_id"]
    event = str(UUID(int=501))
    arbitrary = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(),
        json={"decision": "approved", "idempotency_key": event, "calories": "9999"},
    )
    assert arbitrary.status_code == 422
    first = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(),
        json={"decision": "approved", "idempotency_key": event},
    )
    replay = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(),
        json={"decision": "approved", "idempotency_key": event},
    )
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json()["target_review_decision_id"] == first.json()["target_review_decision_id"]
    assert len(targets.policies) == 2 and len(decisions.outcomes) == 1
    conflict = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(),
        json={"decision": "rejected", "idempotency_key": event},
    )
    hidden = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(USER_B),
        json={"decision": "rejected", "idempotency_key": str(UUID(int=502))},
    )
    assert conflict.status_code == 409
    assert hidden.status_code == 404


def test_rejection_replays_without_target_and_newer_references_block_only_approval() -> None:
    client, goals, targets, _, decisions = _app(trend=_trend(rate="0"))
    review = _review(client)
    review_id = review["review_id"]
    newer = _target(
        version_id=UUID(int=202),
        approved_at=datetime(2026, 8, 27, tzinfo=UTC),
        calories="2550",
    )
    targets.save_approved(newer, "newer", newer.created_at)  # type: ignore[arg-type]
    stale = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(),
        json={"decision": "approved", "idempotency_key": str(UUID(int=510))},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "target_review_stale"
    rejected = client.post(
        f"/v1/target-reviews/{review_id}/decision",
        headers=_headers(),
        json={"decision": "rejected", "idempotency_key": str(UUID(int=511))},
    )
    assert rejected.status_code == 201
    assert rejected.json()["resulting_target_policy_id"] is None
    assert len(targets.policies) == 2 and decisions.decision_logs == {}
    assert goals.latest(USER_A) is not None


def test_m10_routes_fail_closed_without_durable_storage() -> None:
    settings = _settings()
    health = HealthBodyMassSyncUseCase(
        HealthSyncDeps(
            InMemoryHealthBodyMassRepository(), _Clock(datetime(2026, 8, 28, tzinfo=UTC))
        )
    )
    client = TestClient(create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health)))
    latest = client.get("/v1/goal-policies/latest", headers=_headers())
    review = client.post(
        "/v1/target-reviews",
        headers=_headers(),
        json={"as_of_date": AS_OF.isoformat(), "timezone": "UTC"},
    )
    assert latest.status_code == review.status_code == 503
    assert latest.json()["error"]["code"] == "storage_unavailable"
