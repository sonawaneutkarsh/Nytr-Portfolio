"""M6 API tests: auth, plan states, ownership, 409/400 mapping, log redaction."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_planning import (
    get_approval_use_case,
    get_planning_runs,
    get_planning_targets,
)
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.daily_plan import DailyPlanInputs, GenerateDailyPlanUseCase
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.target_policy import ApproveTargetPolicyUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryPlanRunRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import (
    PlanRun,
    PlanRunStatus,
    TargetPolicyVersion,
)
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, PeriodMenu
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey
from tests.unit.planning_helpers import make_offering

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
SUBJECT_A = "00000000-0000-0000-0000-0000000000a1"
SUBJECT_B = "00000000-0000-0000-0000-0000000000b2"
PLAN_DATE = date(2026, 8, 21)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


class _Ids:
    _n = 100

    def new_id(self) -> UUID:
        _Ids._n += 1
        return UUID(int=_Ids._n)


def _token(subject: str = SUBJECT_A, **claims: object) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": subject,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta_minutes(10)).timestamp()),
    }
    payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


def timedelta_minutes(minutes: int):
    from datetime import timedelta

    return timedelta(minutes=minutes)


@pytest.fixture()
def stores() -> tuple[InMemoryPlanRunRepository, InMemoryTargetPolicyRepository]:
    return InMemoryPlanRunRepository(), InMemoryTargetPolicyRepository()


def _client(stores) -> TestClient:
    settings = HealthApiSettings(
        database_url=None, jwt_secret=SECRET, supabase_url=None, jwt_audience=AUDIENCE
    )
    verifier = TokenVerifier(settings)
    health_repo = InMemoryHealthBodyMassRepository()
    health_use_case = HealthBodyMassSyncUseCase(HealthSyncDeps(health_repo, _Clock()))
    app = create_health_app(HealthApiDeps(settings, verifier, health_use_case))
    runs, targets = stores
    app.state.planning_run_repository = runs
    app.state.planning_target_repository = targets
    app.dependency_overrides[get_planning_runs] = lambda: runs
    app.dependency_overrides[get_planning_targets] = lambda: targets
    app.state.planning_approval_use_case = ApproveTargetPolicyUseCase(
        repository=targets, clock=_Clock(), ids=_Ids()
    )
    app.dependency_overrides[get_approval_use_case] = lambda: app.state.planning_approval_use_case
    return TestClient(app)


def _seed_completed_plan(
    runs: InMemoryPlanRunRepository,
    user_id: str = SUBJECT_A,
    target_policy_version_id: UUID | None = None,
) -> str:
    offerings = [make_offering(1).build(), make_offering(2).build()]
    view = MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=tuple(offerings))},
        fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
        snapshot_sha256="api-menu-sha",
    )
    schedule = WeeklySchedule(
        version="api-schedule.v1",
        timezone="America/New_York",
        days={
            **{w: DaySchedule(weekday=w, blocks=()) for w in Weekday},
            Weekday.FRIDAY: DaySchedule(
                weekday=Weekday.FRIDAY,
                blocks=(
                    ScheduleBlock(
                        kind=BlockKind.STACKS_MEAL,
                        start=time(12, 30),
                        end=time(13, 15),
                        label="lunch",
                        meal_context=MealContext.POST_WORKOUT_LUNCH,
                    ),
                ),
            ),
        },
    )
    policy = PlannerPolicy(
        version="api-planner.v1",
        context_period={MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH},
        slot_shares={},
    )
    targets_obj = TargetSet(
        policy_version="api-targets.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal("900"), weight=Decimal("1")
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal("50"), weight=Decimal("1")
            ),
        },
    )
    inputs = DailyPlanInputs(
        user_id=UUID(user_id),
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        schedule=schedule,
        exceptions=(),
        menu=view,
        policy=policy,
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=targets_obj,
        target_policy_version_id=target_policy_version_id,
        offering_profile_ids={
            str(o.offering_id): UUID(int=o.food_id.int % 1000 + 9000) for o in offerings
        },
    )
    use_case = GenerateDailyPlanUseCase(runs=runs, clock=_Clock(), ids=_Ids())
    outcome = use_case.execute(inputs, plan_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
    assert outcome.version is not None
    return outcome.version.plan_sha256


class TestGetPlansDay:
    def test_unauthenticated_401_and_garbage_token_401(self, stores) -> None:
        client = _client(stores)
        url = f"/v1/plans/day?date={PLAN_DATE.isoformat()}"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401

    def test_missing_date_parameter_is_422(self, stores) -> None:
        client = _client(stores)
        response = client.get("/v1/plans/day", headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 422

    def test_not_generated_state_for_unknown_day(self, stores) -> None:
        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        response = client.get("/v1/plans/day?date=2027-01-02", headers=headers)
        assert response.status_code == 200
        assert response.json() == {
            "state": "not_generated",
            "requested_date": "2027-01-02",
        }

    def test_completed_state_returns_hash_without_payload_logging(self, stores, caplog) -> None:
        runs, _ = stores
        sha = _seed_completed_plan(runs)
        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        with caplog.at_level(logging.INFO, logger="nutrition_agent.planning_api"):
            response = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "completed"
        assert body["plan_sha256"] == sha
        assert body["plan"]["status"] == "ok"
        assert body["target_policy"] is None
        assert body["generated_at"] == _Clock().now().isoformat()
        persisted = runs.latest_for_user_date(UUID(SUBJECT_A), PLAN_DATE)
        assert persisted is not None
        expected = [
            {
                "item_id": str(item.item_id),
                "slot_index": item.slot_index,
                "rank": item.rank,
                "candidate_id": item.candidate_id,
            }
            for item in persisted.plan_items
        ]
        assert body["plan_items"] == expected
        assert body["plan_items"][0]["item_id"] != body["plan_items"][0]["candidate_id"]
        # Redaction: log records carry state/sha prefix only.
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "artifact_kind" not in joined
        assert '"lines"' not in joined

    def test_completed_item_projection_is_repeatable_side_effect_free_and_exact(
        self, stores
    ) -> None:
        runs, _ = stores
        sha = _seed_completed_plan(runs)
        before = runs.latest_for_user_date(UUID(SUBJECT_A), PLAN_DATE)
        assert before is not None
        canonical_before = before.plan_canonical
        artifact_before = before.plan_jsonb
        counts_before = (len(runs.runs), len(runs.versions), len(runs.items))
        headers = {"Authorization": f"Bearer {_token()}"}
        client = _client(stores)

        first = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        second = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)

        assert first.status_code == second.status_code == 200
        assert first.json()["plan_items"] == second.json()["plan_items"]
        assert first.json()["plan_sha256"] == sha
        after = runs.latest_for_user_date(UUID(SUBJECT_A), PLAN_DATE)
        assert after is not None
        assert after.plan_canonical == canonical_before
        assert after.plan_jsonb == artifact_before
        assert after.plan_sha256 == sha
        assert (len(runs.runs), len(runs.versions), len(runs.items)) == counts_before

    def test_missing_or_mismatched_item_projection_fails_closed(self, stores) -> None:
        from dataclasses import replace

        runs, _ = stores
        _seed_completed_plan(runs)
        view = runs.latest_for_user_date(UUID(SUBJECT_A), PLAN_DATE)
        assert view is not None
        run_id = view.run_id
        original = list(runs.items[run_id])
        headers = {"Authorization": f"Bearer {_token()}"}
        client = _client(stores)

        runs.items[run_id] = []
        missing = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        assert missing.status_code == 503
        assert missing.json()["error"]["code"] == "storage_inconsistent"

        runs.items[run_id] = [
            replace(original[0], candidate_id="wrong-candidate"),
            *original[1:],
        ]
        mismatched = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        assert mismatched.status_code == 503
        assert mismatched.json()["error"]["code"] == "storage_inconsistent"

    def test_no_plan_state_carries_reason_codes(self, stores) -> None:
        runs, _ = stores
        # Friday lunch slot exists; source VALIDATED the period as empty =>
        # planner NO_PLAN with the empty_menu_period reason code preserved.
        empty_lunch = MenuDayView(
            service_date=PLAN_DATE,
            periods={MealPeriod.LUNCH: PeriodMenu(offerings=(), explicitly_empty=True)},
            fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
            snapshot_sha256="empty-sha",
        )
        schedule = WeeklySchedule(
            version="s.v1",
            timezone="America/New_York",
            days={
                **{w: DaySchedule(weekday=w, blocks=()) for w in Weekday},
                Weekday.FRIDAY: DaySchedule(
                    weekday=Weekday.FRIDAY,
                    blocks=(
                        ScheduleBlock(
                            kind=BlockKind.STACKS_MEAL,
                            start=time(12, 30),
                            end=time(13, 15),
                            label="lunch",
                            meal_context=MealContext.POST_WORKOUT_LUNCH,
                        ),
                    ),
                ),
            },
        )
        base = _minimal_inputs(runs, view=empty_lunch, snapshot="empty-sha")
        from dataclasses import replace

        inputs = replace(base, schedule=schedule)
        use_case = GenerateDailyPlanUseCase(runs=runs, clock=_Clock(), ids=_Ids())
        outcome = use_case.execute(inputs, plan_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        assert outcome.run is not None and outcome.run.status.value == "no_plan"
        assert outcome.run.reason_codes == ("empty_menu_period",)

        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        response = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        body = response.json()
        assert response.status_code == 200
        assert body["state"] == "no_plan"
        assert body["reason_codes"] == ["empty_menu_period"]
        assert set(body) == {
            "state",
            "requested_date",
            "reason_codes",
            "inputs_fingerprint",
        }

    def test_completed_plan_uses_historical_policy_and_persisted_generation_time(
        self, stores
    ) -> None:
        runs, targets = stores
        policy_one = TargetPolicyVersion(
            version_id=UUID(int=8_001),
            user_id=UUID(SUBJECT_A),
            policy_version="historical-p1",
            goals_jsonb=[dict(GOALS_OK[0])],
            payload_sha256="1" * 64,
            created_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        )
        targets.save_approved(
            policy_one,
            rationale="first",
            decided_by_clock=policy_one.created_at,
        )
        _seed_completed_plan(runs, target_policy_version_id=policy_one.version_id)
        policy_two = TargetPolicyVersion(
            version_id=UUID(int=8_002),
            user_id=UUID(SUBJECT_A),
            policy_version="current-p2",
            goals_jsonb=[{**GOALS_OK[0], "value": "950"}],
            payload_sha256="2" * 64,
            created_at=datetime(2026, 8, 21, 13, 0, tzinfo=UTC),
        )
        targets.save_approved(
            policy_two,
            rationale="second",
            decided_by_clock=policy_two.created_at,
        )
        assert targets.latest_approved(UUID(SUBJECT_A)) == policy_two

        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        first = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        second = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["target_policy"] == {
            "version_id": str(policy_one.version_id),
            "policy_version": policy_one.policy_version,
            "payload_sha256": policy_one.payload_sha256,
            "approved_at": policy_one.created_at.isoformat(),
        }
        assert first.json()["generated_at"] == _Clock().now().isoformat()
        assert len(runs.runs) == 1

    def test_completed_plan_missing_artifact_still_fails_closed(self, stores) -> None:
        runs, _ = stores
        run = PlanRun(
            run_id=UUID(int=9_001),
            user_id=UUID(SUBJECT_A),
            requested_for_date=PLAN_DATE,
            timezone="America/New_York",
            inputs_fingerprint="missing-artifact",
            status=PlanRunStatus.COMPLETED,
            reason_codes=(),
            started_at=_Clock().now(),
            finished_at=_Clock().now(),
        )
        runs.runs[run.run_id] = run

        response = _client(stores).get(
            f"/v1/plans/day?date={PLAN_DATE.isoformat()}",
            headers={"Authorization": f"Bearer {_token()}"},
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_inconsistent"

    def test_cross_user_access_returns_not_generated(self, stores) -> None:
        runs, _ = stores
        _seed_completed_plan(runs, SUBJECT_A)
        client = _client(stores)
        b_headers = {"Authorization": f"Bearer {_token(SUBJECT_B)}"}
        a_headers = {"Authorization": f"Bearer {_token(SUBJECT_A)}"}
        b_response = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=b_headers)
        a_response = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=a_headers)
        assert a_response.json()["state"] == "completed"
        assert b_response.json()["state"] == "not_generated"


def _minimal_inputs(runs, view: MenuDayView, snapshot: str) -> DailyPlanInputs:
    del runs, snapshot
    return DailyPlanInputs(
        user_id=UUID(SUBJECT_A),
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        schedule=WeeklySchedule(
            version="s.v1",
            timezone="America/New_York",
            days={w: DaySchedule(weekday=w, blocks=()) for w in Weekday},
        ),
        exceptions=(),
        menu=view,
        policy=PlannerPolicy(
            version="p.v1",
            context_period={MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH},
            slot_shares={},
        ),
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=TargetSet(policy_version="t.v1", goals={}),
        target_policy_version_id=None,
        offering_profile_ids={},
    )


GOALS_OK = [
    {"nutrient": "calories_kcal", "kind": "target", "value": "900", "weight": "1"},
    {"nutrient": "protein_g", "kind": "target", "value": "50", "weight": "1"},
]


class TestPostTargetPolicies:
    def test_unauthenticated_401(self, stores) -> None:
        client = _client(stores)
        assert client.post("/v1/target-policies", json={"policy_version": "v"}).status_code == 401

    def test_happy_path_response_fields(self, stores, caplog) -> None:
        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        with caplog.at_level(logging.INFO, logger="nutrition_agent.planning_api"):
            response = client.post(
                "/v1/target-policies",
                json={"policy_version": "leanbulk-v1", "rationale": "start", "goals": GOALS_OK},
                headers=headers,
            )
        assert response.status_code == 200
        body = response.json()
        assert body["policy_version"] == "leanbulk-v1"
        assert len(body["payload_sha256"]) == 64
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "900" not in joined and '"value"' not in joined
        assert "subject_hash" in joined

    def test_duplicate_version_conflicts_409(self, stores) -> None:
        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        payload = {
            "policy_version": "v1",
            "rationale": "r",
            "goals": GOALS_OK,
        }
        first = client.post("/v1/target-policies", json=payload, headers=headers)
        second = client.post("/v1/target-policies", json=payload, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "duplicate_target_policy_version"

    @pytest.mark.parametrize(
        ("payload"),
        [
            {"rationale": "r", "goals": GOALS_OK},
            {"policy_version": "v1", "goals": GOALS_OK},
            {"policy_version": "v1", "rationale": ""},
            {"policy_version": "v1", "rationale": "r", "goals": []},
            {
                "policy_version": "v1",
                "rationale": "r",
                "goals": [
                    {"nutrient": "warp_core_temp", "kind": "target", "value": "1", "weight": "1"}
                ],
            },
            {
                "policy_version": "v1",
                "rationale": "r",
                "goals": [
                    {"nutrient": "calories_kcal", "kind": "target", "value": "NaN", "weight": "1"}
                ],
            },
        ],
    )
    def test_malformed_payload_400_envelope(self, stores, payload) -> None:
        client = _client(stores)
        headers = {"Authorization": f"Bearer {_token()}"}
        response = client.post("/v1/target-policies", json=payload, headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_target_policy"

    def test_cross_user_duplicate_not_visible(self, stores) -> None:
        client = _client(stores)
        payload = {"policy_version": "shared-label", "rationale": "r", "goals": GOALS_OK}
        a_headers = {"Authorization": f"Bearer {_token(SUBJECT_A)}"}
        b_headers = {"Authorization": f"Bearer {_token(SUBJECT_B)}"}
        assert (
            client.post("/v1/target-policies", json=payload, headers=a_headers).status_code == 200
        )
        assert (
            client.post("/v1/target-policies", json=payload, headers=b_headers).status_code == 200
        )


class TestNoDsnFailClosed:
    """ADR-019: without a durable DSN, planning routes must 503 — never run
    against throwaway in-memory state. Explicit dependency injection of
    in-memory repositories (the other tests above) remains allowed."""

    def _client_no_dsn(self) -> tuple[TestClient, object]:
        settings = HealthApiSettings(
            database_url=None, jwt_secret=SECRET, supabase_url=None, jwt_audience=AUDIENCE
        )
        verifier = TokenVerifier(settings)
        health_use_case = HealthBodyMassSyncUseCase(
            HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock())
        )
        app = create_health_app(HealthApiDeps(settings, verifier, health_use_case))
        return TestClient(app), app

    def test_plans_day_503_without_dsn(self) -> None:
        client, app = self._client_no_dsn()
        headers = {"Authorization": f"Bearer {_token()}"}
        response = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}", headers=headers)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        # Fail closed, not fail-open: no in-memory planning repository wired.
        assert not isinstance(app.state.planning_run_repository, InMemoryPlanRunRepository)

    def test_target_policies_503_without_dsn(self) -> None:
        client, app = self._client_no_dsn()
        headers = {"Authorization": f"Bearer {_token()}"}
        response = client.post(
            "/v1/target-policies",
            json={"policy_version": "v1", "rationale": "r", "goals": GOALS_OK},
            headers=headers,
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        # Nothing was persisted: no in-memory repository exists to accept it.
        assert not isinstance(app.state.planning_target_repository, InMemoryTargetPolicyRepository)

    def test_no_dsn_fail_closed_still_requires_auth_first(self) -> None:
        client, _ = self._client_no_dsn()
        response = client.get(f"/v1/plans/day?date={PLAN_DATE.isoformat()}")
        assert response.status_code == 401
