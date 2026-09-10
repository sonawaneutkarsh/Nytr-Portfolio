"""M7 Step 6 API tests for explicit demand-driven daily-plan generation."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_generation import (
    get_generation_clock,
    get_generation_use_case,
    get_server_inputs_provider,
)
from nutrition_agent.api.routes_planning import get_planning_runs, get_planning_targets
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.daily_plan import GenerateDailyPlanUseCase
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.ports import DuplicateLogicalPlanError, ResolvedMenuDay
from nutrition_agent.application.server_inputs import (
    PRODUCTION_SERVER_CONFIGURATION,
    DefaultServerInputsProvider,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryMenuDayReadRepository,
    InMemoryPlanRunRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.planning.artifacts import (
    PlanItem,
    PlanRun,
    PlanVersion,
    TargetPolicyVersion,
)
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.schedule import DaySchedule, Weekday, WeeklySchedule
from nutrition_agent.domain.stacks.entities import MealPeriod, NutritionSourceState
from tests.unit.planning_helpers import make_offering

SECRET = "generation-test-secret-for-hs256-signing"
AUDIENCE = "authenticated"
SUBJECT = "00000000-0000-0000-0000-0000000000a1"
PLAN_DATE = date(2026, 8, 21)
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
GOALS = [
    {"kind": "target", "nutrient": "calories_kcal", "value": "900", "weight": "1"},
    {"kind": "target", "nutrient": "protein_g", "value": "50", "weight": "1"},
]


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def __init__(self) -> None:
        self._value = 10_000

    def new_id(self) -> UUID:
        self._value += 1
        return UUID(int=self._value)


class _RacePlanRunRepository(InMemoryPlanRunRepository):
    def save(
        self,
        run: PlanRun,
        version: PlanVersion,
        items: Sequence[PlanItem],
    ) -> None:
        del run, version, items
        raise DuplicateLogicalPlanError("simulated unique race")


@dataclass
class _Harness:
    app: FastAPI
    client: TestClient
    runs: InMemoryPlanRunRepository
    targets: InMemoryTargetPolicyRepository
    menu_days: InMemoryMenuDayReadRepository


def _token(subject: str = SUBJECT) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def _resolved_menu(*, snapshot_sha256: str, empty: bool = False) -> ResolvedMenuDay:
    if empty:
        periods = {
            MealPeriod.LUNCH: PeriodMenu(offerings=(), explicitly_empty=True),
            MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True),
        }
        pins: dict[str, UUID] = {}
    else:
        lunch = cast(OfferingView, make_offering(1).build())
        dinner = cast(OfferingView, make_offering(2).build())
        periods = {
            MealPeriod.LUNCH: PeriodMenu(offerings=(lunch,)),
            MealPeriod.DINNER: PeriodMenu(offerings=(dinner,)),
        }
        pins = {
            str(lunch.offering_id): UUID(int=9_001),
            str(dinner.offering_id): UUID(int=9_002),
        }
    return ResolvedMenuDay(
        menu=MenuDayView(
            service_date=PLAN_DATE,
            periods=periods,
            fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
            snapshot_sha256=snapshot_sha256,
        ),
        offering_profile_ids=pins,
    )


def _resolved_estimated_menu() -> ResolvedMenuDay:
    lunch = replace(
        cast(
            OfferingView,
            make_offering(31, name="CYO Halal Bowl", with_profile=False).build(),
        ),
        nutrition_source_state=NutritionSourceState.SOURCE_PLACEHOLDER,
        nutrition_snapshot_sha256="lunch-placeholder-sha",
    )
    dinner = replace(
        cast(
            OfferingView,
            make_offering(32, name="CYO Halal Bowl", with_profile=False).build(),
        ),
        nutrition_source_state=NutritionSourceState.SOURCE_PLACEHOLDER,
        nutrition_snapshot_sha256="dinner-placeholder-sha",
    )
    return ResolvedMenuDay(
        menu=MenuDayView(
            service_date=PLAN_DATE,
            periods={
                MealPeriod.LUNCH: PeriodMenu(offerings=(lunch,)),
                MealPeriod.DINNER: PeriodMenu(offerings=(dinner,)),
            },
            fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
            snapshot_sha256="estimated-menu-sha",
            campus_id=50,
        ),
        offering_profile_ids={},
    )


def _seed_policy(
    targets: InMemoryTargetPolicyRepository,
    *,
    version_id: UUID,
    label: str,
    created_at: datetime,
    goals: list[dict[str, str]] | None = None,
) -> TargetPolicyVersion:
    policy = TargetPolicyVersion(
        version_id=version_id,
        user_id=UUID(SUBJECT),
        policy_version=label,
        goals_jsonb=[dict(goal) for goal in (goals or GOALS)],
        payload_sha256=f"payload-{label}",
        created_at=created_at,
    )
    targets.save_approved(policy, rationale="approved", decided_by_clock=created_at)
    return policy


def _harness(
    *,
    menu: ResolvedMenuDay | None = None,
    runs: InMemoryPlanRunRepository | None = None,
) -> _Harness:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    verifier = TokenVerifier(settings)
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, verifier, health))
    plan_runs = runs or InMemoryPlanRunRepository()
    targets = InMemoryTargetPolicyRepository()
    menu_days = InMemoryMenuDayReadRepository(days={PLAN_DATE: menu} if menu is not None else {})
    provider = DefaultServerInputsProvider(menu_days=menu_days)
    generation = GenerateDailyPlanUseCase(runs=plan_runs, clock=_Clock(), ids=_Ids())

    app.state.planning_run_repository = plan_runs
    app.state.planning_target_repository = targets
    app.state.planning_inputs_provider = provider
    app.state.planning_generation_use_case = generation
    app.state.planning_clock = _Clock()
    app.dependency_overrides[get_planning_runs] = lambda: plan_runs
    app.dependency_overrides[get_planning_targets] = lambda: targets
    app.dependency_overrides[get_server_inputs_provider] = lambda: provider
    app.dependency_overrides[get_generation_use_case] = lambda: generation
    app.dependency_overrides[get_generation_clock] = _Clock
    return _Harness(app, TestClient(app), plan_runs, targets, menu_days)


def _post(client: TestClient, *, payload: dict[str, object] | None = None):
    return client.post(
        "/v1/plans/day/generate",
        json=(
            payload
            if payload is not None
            else {
                "date": PLAN_DATE.isoformat(),
                "timezone": "America/New_York",
            }
        ),
        headers=_headers(),
    )


def test_generation_requires_valid_bearer_authentication() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="auth-sha"))
    body = {"date": PLAN_DATE.isoformat(), "timezone": "America/New_York"}

    missing = harness.client.post("/v1/plans/day/generate", json=body)
    garbage = harness.client.post(
        "/v1/plans/day/generate",
        json=body,
        headers={"Authorization": "Bearer garbage"},
    )

    assert missing.status_code == garbage.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_token"
    assert garbage.json()["error"]["code"] == "invalid_token"


@pytest.mark.parametrize(
    "payload",
    [
        {"date": "not-a-date", "timezone": "America/New_York"},
        {"date": PLAN_DATE.isoformat()},
        {
            "date": PLAN_DATE.isoformat(),
            "timezone": "America/New_York",
            "schedule": {"client": "must not control this"},
        },
        {
            "date": PLAN_DATE.isoformat(),
            "timezone": "America/New_York",
            "is_training_day": True,
        },
    ],
)
def test_malformed_or_authoritative_client_inputs_are_rejected(payload: dict[str, object]) -> None:
    response = _post(_harness().client, payload=payload)
    assert response.status_code == 422


def test_invalid_timezone_is_400_invalid_request() -> None:
    response = _post(
        _harness().client,
        payload={"date": PLAN_DATE.isoformat(), "timezone": "Mars/Olympus_Mons"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "invalid_request", "detail": "timezone is not recognized"}
    }


def test_missing_approved_policy_is_409_without_persisting_a_run() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="no-policy-sha"))
    response = _post(harness.client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_approved_target_policy"
    assert harness.runs.runs == {}


def test_completed_generation_persists_policy_and_replays_with_get_parity() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="completed-sha"))
    policy = _seed_policy(
        harness.targets,
        version_id=UUID(int=701),
        label="generation-p1",
        created_at=NOW,
    )

    first = _post(harness.client)
    replay = _post(harness.client)

    assert first.status_code == replay.status_code == 200
    first_body = first.json()
    assert first_body["state"] == "completed"
    assert first_body == replay.json()
    assert len(first_body["plan_sha256"]) == 64
    assert len(first_body["inputs_fingerprint"]) == 64
    assert first_body["plan"]["status"] == "ok"
    assert len(harness.runs.runs) == 1
    stored_run = next(iter(harness.runs.runs.values()))
    assert stored_run.target_policy_version_id == policy.version_id
    assert str(stored_run.run_id) == first_body["run_id"]
    assert str(harness.runs.versions[stored_run.run_id].version_id) == first_body["version_id"]

    get_response = harness.client.get(
        f"/v1/plans/day?date={PLAN_DATE.isoformat()}",
        headers=_headers(),
    )
    assert get_response.status_code == 200
    get_body = get_response.json()
    for key in ("run_id", "version_id", "plan_sha256", "inputs_fingerprint", "plan"):
        assert get_body[key] == first_body[key]
    assert len(harness.runs.runs) == 1


def test_generation_api_exposes_estimated_candidate_without_official_profile() -> None:
    harness = _harness(menu=_resolved_estimated_menu())
    _seed_policy(
        harness.targets,
        version_id=UUID(int=710),
        label="estimated-policy",
        created_at=NOW,
        goals=[
            {
                "kind": "target",
                "nutrient": "calories_kcal",
                "value": "1500",
                "weight": "1",
            },
            {
                "kind": "target",
                "nutrient": "protein_g",
                "value": "46",
                "weight": "1",
            },
        ],
    )

    response = _post(harness.client)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "completed"
    for slot in body["plan"]["slots"]:
        candidate = slot["candidates"][0]
        assert candidate["candidate_kind"] == "configurable_estimate"
        assert candidate["lines"] == []
        assert candidate["provenance"]["profile_content_sha256s"] == []
        estimate = candidate["configurable_estimate"]
        assert estimate["definition"]["display_name"] == "CYO Halal Bowl"
        assert estimate["availability"]["nutrition_source_state"] == "source_placeholder"
        assert "uncertainty:estimated_nutrition" in candidate["score"]["breakdown"]
    stored_items = tuple(item for run_items in harness.runs.items.values() for item in run_items)
    assert stored_items
    assert all(item.profile_row_ids == () for item in stored_items)
    assert all(item.profile_content_sha256s == () for item in stored_items)

    get_response = harness.client.get(
        f"/v1/plans/day?date={PLAN_DATE.isoformat()}",
        headers=_headers(),
    )
    assert get_response.status_code == 200
    get_body = get_response.json()
    for key in ("run_id", "version_id", "plan_sha256", "inputs_fingerprint", "plan"):
        assert get_body[key] == body[key]
    assert len(harness.runs.runs) == 1


def test_valid_authoritative_empty_menu_returns_no_plan_with_verbatim_reason() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="empty-sha", empty=True))
    policy = _seed_policy(
        harness.targets,
        version_id=UUID(int=702),
        label="empty-policy",
        created_at=NOW,
    )

    response = _post(harness.client)
    body = response.json()
    assert response.status_code == 200
    assert body["state"] == "no_plan"
    assert body["reason_codes"] == ["empty_menu_period"]
    assert "plan" not in body
    assert len(harness.runs.runs) == 1
    stored = next(iter(harness.runs.runs.values()))
    assert stored.target_policy_version_id == policy.version_id


def test_zero_slot_reason_is_exposed_verbatim_by_generation_api() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="zero-slot-sha"))
    empty_schedule = WeeklySchedule(
        version="api-empty-schedule.v1",
        timezone="America/New_York",
        days={weekday: DaySchedule(weekday=weekday, blocks=()) for weekday in Weekday},
    )
    provider = DefaultServerInputsProvider(
        menu_days=harness.menu_days,
        configuration=replace(
            PRODUCTION_SERVER_CONFIGURATION,
            schedule=empty_schedule,
        ),
    )
    harness.app.state.planning_inputs_provider = provider
    harness.app.dependency_overrides[get_server_inputs_provider] = lambda: provider
    _seed_policy(
        harness.targets,
        version_id=UUID(int=709),
        label="zero-slot-policy",
        created_at=NOW,
    )

    response = _post(harness.client)

    assert response.status_code == 200
    assert response.json()["state"] == "no_plan"
    assert response.json()["reason_codes"] == ["no_resolved_meal_slots"]
    stored_run = next(iter(harness.runs.runs.values()))
    stored_version = harness.runs.versions[stored_run.run_id]
    assert stored_version.plan_jsonb["failure_reasons"] == ["no_resolved_meal_slots"]


def test_new_target_policy_creates_new_fingerprint_without_mutating_old_run() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="policy-change-sha"))
    first_policy = _seed_policy(
        harness.targets,
        version_id=UUID(int=703),
        label="policy-p1",
        created_at=NOW,
    )
    first = _post(harness.client).json()

    second_goals = [dict(goal) for goal in GOALS]
    second_goals[0]["value"] = "950"
    second_policy = _seed_policy(
        harness.targets,
        version_id=UUID(int=704),
        label="policy-p2",
        created_at=NOW + timedelta(minutes=1),
        goals=second_goals,
    )
    second = _post(harness.client).json()

    assert first["inputs_fingerprint"] != second["inputs_fingerprint"]
    assert len(harness.runs.runs) == 2
    runs_by_id = {str(run.run_id): run for run in harness.runs.runs.values()}
    assert runs_by_id[first["run_id"]].target_policy_version_id == first_policy.version_id
    assert runs_by_id[second["run_id"]].target_policy_version_id == second_policy.version_id


def test_authoritative_menu_snapshot_change_creates_new_logical_run() -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="menu-a"))
    _seed_policy(
        harness.targets,
        version_id=UUID(int=705),
        label="menu-policy",
        created_at=NOW,
    )
    first = _post(harness.client).json()
    harness.menu_days.days[PLAN_DATE] = _resolved_menu(snapshot_sha256="menu-b")
    second = _post(harness.client).json()

    assert first["inputs_fingerprint"] != second["inputs_fingerprint"]
    assert first["run_id"] != second["run_id"]
    assert len(harness.runs.runs) == 2


def test_duplicate_race_maps_to_409_concurrent_generation() -> None:
    harness = _harness(
        menu=_resolved_menu(snapshot_sha256="race-sha"),
        runs=_RacePlanRunRepository(),
    )
    _seed_policy(
        harness.targets,
        version_id=UUID(int=706),
        label="race-policy",
        created_at=NOW,
    )

    response = _post(harness.client)
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "concurrent_generation",
            "detail": "another generation request completed or is completing",
        }
    }


def test_menu_unavailable_is_503_and_does_not_persist() -> None:
    harness = _harness()
    _seed_policy(
        harness.targets,
        version_id=UUID(int=707),
        label="missing-menu-policy",
        created_at=NOW,
    )
    response = _post(harness.client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "menu_data_unavailable"
    assert harness.runs.runs == {}


def test_no_dsn_generation_fails_closed_with_storage_unavailable() -> None:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    response = _post(TestClient(app))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert not isinstance(app.state.planning_run_repository, InMemoryPlanRunRepository)


def test_generation_logs_only_redacted_metadata(caplog: pytest.LogCaptureFixture) -> None:
    harness = _harness(menu=_resolved_menu(snapshot_sha256="redaction-sha"))
    sensitive_calories = "901.23456789"
    sensitive_protein = "51.987654321"
    _seed_policy(
        harness.targets,
        version_id=UUID(int=708),
        label="redaction-policy",
        created_at=NOW,
        goals=[
            {
                "kind": "target",
                "nutrient": "calories_kcal",
                "value": sensitive_calories,
                "weight": "1",
            },
            {
                "kind": "target",
                "nutrient": "protein_g",
                "value": sensitive_protein,
                "weight": "1",
            },
        ],
    )
    token = _token()
    with caplog.at_level(logging.INFO, logger="nutrition_agent.generation_api"):
        response = harness.client.post(
            "/v1/plans/day/generate",
            json={"date": PLAN_DATE.isoformat(), "timezone": "America/New_York"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "subject_hash" in joined
    for sensitive in (
        token,
        SUBJECT,
        sensitive_calories,
        sensitive_protein,
        "calories_kcal",
        "protein_g",
        "artifact_kind",
        '"slots"',
    ):
        assert sensitive not in joined
