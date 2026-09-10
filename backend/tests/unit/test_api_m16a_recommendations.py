from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_recommendations import (
    get_next_meal_repository,
    get_protein_repository,
)
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryNextMealRecommendationRepository,
    InMemoryProteinTargetProposalRepository,
)
from nutrition_agent.domain.next_meal import (
    NextMealStatus,
    recommendation_from_artifact,
)
from nutrition_agent.domain.nutrition.targets import GoalKind
from nutrition_agent.domain.protein_target import (
    ProteinProposalDecisionValue,
    ProteinTargetProposal,
    ProteinTargetProposalDecision,
)

SECRET = "m16a-recommendation-api-secret-for-tests"
AUDIENCE = "authenticated"
OWNER = UUID(int=101)
OTHER = UUID(int=202)
NOW = datetime(2026, 9, 5, 18, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _headers(user_id: UUID) -> dict[str, str]:
    issued = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    repository = InMemoryNextMealRecommendationRepository()
    artifact: dict[str, object] = {
        "artifact_kind": "next_meal_recommendation",
        "artifact_version": "m16a.v1",
        "decision_at": NOW.isoformat(),
        "local_date": "2026-09-05",
        "timezone": "UTC",
        "next_meal_policy_version": "next-meal.remaining-opportunities.v1",
        "status": "no_remaining_meal_opportunity",
        "reason_codes": ["all_stacks_windows_elapsed"],
    }
    repository.save(
        recommendation_from_artifact(
            recommendation_id=UUID(int=1),
            user_id=OWNER,
            client_request_id=UUID(int=2),
            local_date=date(2026, 9, 5),
            timezone="UTC",
            decision_at=NOW,
            target_policy_version_id=None,
            status=NextMealStatus.NO_REMAINING_MEAL_OPPORTUNITY,
            reason_codes=("all_stacks_windows_elapsed",),
            artifact=artifact,
        )
    )
    app.dependency_overrides[get_next_meal_repository] = lambda: repository
    return TestClient(app)


def test_latest_is_authenticated_retrieval_only_and_owner_isolated() -> None:
    client = _client()
    path = "/v1/recommendations/next-meal/latest"
    assert client.get(path).status_code == 401
    owner = client.get(path, headers=_headers(OWNER))
    assert owner.status_code == 200
    assert owner.json()["status"] == "no_remaining_meal_opportunity"
    assert "created" not in owner.json()
    assert client.get(path, headers=_headers(OTHER)).status_code == 404


def test_generation_rejects_client_authoritative_owner_and_totals() -> None:
    response = _client().post(
        "/v1/recommendations/next-meal",
        headers=_headers(OWNER),
        json={
            "local_date": "2026-09-05",
            "timezone": "UTC",
            "client_request_id": str(UUID(int=55)),
            "user_id": str(OTHER),
            "remaining_calories": "9999",
        },
    )
    assert response.status_code == 422


def test_latest_protein_proposal_reports_terminal_decision_and_owner_isolation() -> None:
    client = _client()
    repository = InMemoryProteinTargetProposalRepository()
    proposal = ProteinTargetProposal(
        proposal_id=UUID(int=70),
        user_id=OWNER,
        prior_target_policy_version_id=UUID(int=71),
        body_mass_sample_uuid=UUID(int=72),
        policy_version="owner-protein-target.v1",
        target_kind=GoalKind.FLOOR,
        body_mass_kg=Decimal("67.5"),
        grams_per_pound=Decimal("0.8"),
        proposed_protein_g=Decimal("119"),
        evidence_digest="c" * 64,
        calculation_payload={"formula": "pinned"},
        rationale="proposal",
        provenance="HealthKit",
        generated_at=NOW,
    )
    repository.save(proposal)
    decision = ProteinTargetProposalDecision(
        decision_id=UUID(int=73),
        user_id=OWNER,
        proposal_id=proposal.proposal_id,
        decision=ProteinProposalDecisionValue.REJECTED,
        rationale="owner declined",
        client_event_id=UUID(int=74),
        resulting_target_policy_version_id=None,
        decided_at=NOW,
    )
    repository.decide(proposal, decision, None, None)
    client.app.dependency_overrides[get_protein_repository] = lambda: repository

    owner = client.get("/v1/target-policies/protein-proposals/latest", headers=_headers(OWNER))
    assert owner.status_code == 200
    assert owner.json()["decision_status"] == "rejected"
    assert owner.json()["requires_explicit_approval"] is False
    assert owner.json()["decision"]["rationale"] == "owner declined"
    assert (
        client.get(
            "/v1/target-policies/protein-proposals/latest", headers=_headers(OTHER)
        ).status_code
        == 404
    )
