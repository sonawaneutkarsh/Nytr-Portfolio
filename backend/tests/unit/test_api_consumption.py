"""M7 Step 7 HTTP tests for immutable consumption event APIs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_consumption import (
    get_consumption_id_generator,
    get_list_consumption_use_case,
    get_record_consumption_use_case,
)
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.consumption import (
    ListConsumptionForRunUseCase,
    RecordConsumptionUseCase,
)
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import (
    InMemoryConsumptionRepository,
    InMemoryHealthBodyMassRepository,
)
from nutrition_agent.domain.consumption import ConsumptionEntry, ConsumptionState

SECRET = "consumption-api-test-secret-for-hs256"
AUDIENCE = "authenticated"
USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
USER_B = UUID("00000000-0000-0000-0000-0000000000b2")
RUN_A = UUID("10000000-0000-0000-0000-000000000001")
RUN_B = UUID("10000000-0000-0000-0000-000000000002")
VERSION_A = UUID("20000000-0000-0000-0000-000000000001")
ITEM_A = UUID("30000000-0000-0000-0000-000000000001")
EVENT_A = UUID("40000000-0000-0000-0000-000000000001")
EVENT_B = UUID("40000000-0000-0000-0000-000000000002")
TIME_A = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        value = TIME_A + timedelta(minutes=self.calls)
        self.calls += 1
        return value


class _Ids:
    def __init__(self, start: int) -> None:
        self._value = start
        self.calls = 0

    def new_id(self) -> UUID:
        self._value += 1
        self.calls += 1
        return UUID(int=self._value)


class _HiddenTargetError(Exception):
    sqlstate = "42501"


class _HiddenTargetRepository(InMemoryConsumptionRepository):
    def save(self, entry: ConsumptionEntry):
        del entry
        raise _HiddenTargetError("do not expose foreign hierarchy")


@dataclass
class _Harness:
    app: FastAPI
    client: TestClient
    repository: InMemoryConsumptionRepository
    clock: _Clock
    entry_ids: _Ids
    event_ids: _Ids


def _settings() -> HealthApiSettings:
    return HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )


def _token(user_id: UUID = USER_A) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _headers(user_id: UUID = USER_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def _harness(
    repository: InMemoryConsumptionRepository | None = None,
) -> _Harness:
    settings = _settings()
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    stored = repository if repository is not None else InMemoryConsumptionRepository()
    clock = _Clock()
    entry_ids = _Ids(10_000)
    event_ids = _Ids(20_000)
    record = RecordConsumptionUseCase(stored, clock, entry_ids)
    listing = ListConsumptionForRunUseCase(stored)
    app.state.planning_consumption_repository = stored
    app.state.planning_record_consumption_use_case = record
    app.state.planning_list_consumption_use_case = listing
    app.state.planning_id_generator = event_ids
    app.dependency_overrides[get_record_consumption_use_case] = lambda: record
    app.dependency_overrides[get_list_consumption_use_case] = lambda: listing
    app.dependency_overrides[get_consumption_id_generator] = lambda: event_ids
    return _Harness(app, TestClient(app), stored, clock, entry_ids, event_ids)


def _body(
    *,
    event_id: UUID | None = EVENT_A,
    state: str = "eaten",
) -> dict[str, str]:
    body = {
        "plan_version_id": str(VERSION_A),
        "item_id": str(ITEM_A),
        "state": state,
    }
    if event_id is not None:
        body["client_event_id"] = str(event_id)
    return body


def _post(
    harness: _Harness,
    *,
    run_id: UUID = RUN_A,
    event_id: UUID | None = EVENT_A,
    state: str = "eaten",
    user_id: UUID = USER_A,
):
    return harness.client.post(
        f"/v1/plans/{run_id}/consumption",
        json=_body(event_id=event_id, state=state),
        headers=_headers(user_id),
    )


def test_consumption_routes_require_valid_authentication() -> None:
    harness = _harness()
    path = f"/v1/plans/{RUN_A}/consumption"

    post_missing = harness.client.post(path, json=_body())
    post_invalid = harness.client.post(
        path,
        json=_body(),
        headers={"Authorization": "Bearer garbage"},
    )
    get_missing = harness.client.get(path)

    assert post_missing.status_code == post_invalid.status_code == get_missing.status_code == 401


def test_first_write_is_201_and_exact_replay_is_200_with_original_metadata() -> None:
    harness = _harness()

    first = _post(harness)
    replay = _post(harness)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["state"] == "eaten"
    assert replay.json()["client_event_id"] == str(EVENT_A)
    assert harness.clock.calls == harness.entry_ids.calls == 2
    entries = harness.repository.list_for_run(USER_A, RUN_A)
    assert len(entries) == 1
    assert str(entries[0].entry_id) == first.json()["entry_id"]
    assert entries[0].recorded_at.isoformat() == first.json()["recorded_at"]


def test_conflicting_state_and_run_return_409_without_mutating_original() -> None:
    harness = _harness()
    first = _post(harness)

    state_conflict = _post(harness, state="skipped")
    run_conflict = _post(harness, run_id=RUN_B)

    assert first.status_code == 201
    for response in (state_conflict, run_conflict):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "consumption_conflict"
    entries = harness.repository.list_for_run(USER_A, RUN_A)
    assert len(entries) == 1
    assert entries[0].state is ConsumptionState.EATEN
    assert harness.repository.list_for_run(USER_A, RUN_B) == ()


def test_omitted_client_event_id_is_generated_returned_and_persisted() -> None:
    harness = _harness()

    response = _post(harness, event_id=None)

    assert response.status_code == 201
    generated = UUID(response.json()["client_event_id"])
    assert generated == UUID(int=20_001)
    assert harness.event_ids.calls == 1
    assert harness.repository.list_for_run(USER_A, RUN_A)[0].client_event_id == generated


def test_different_client_event_id_creates_second_immutable_event() -> None:
    harness = _harness()

    first = _post(harness, event_id=EVENT_A)
    second = _post(harness, event_id=EVENT_B, state="skipped")

    assert first.status_code == second.status_code == 201
    entries = harness.repository.list_for_run(USER_A, RUN_A)
    assert len(entries) == 2
    assert tuple(entry.client_event_id for entry in entries) == (EVENT_A, EVENT_B)


def test_invalid_state_and_uuids_use_existing_validation_semantics() -> None:
    harness = _harness()

    invalid_state = _post(harness, state="consumed")
    invalid_path = harness.client.post(
        "/v1/plans/not-a-uuid/consumption",
        json=_body(),
        headers=_headers(),
    )
    invalid_body = harness.client.post(
        f"/v1/plans/{RUN_A}/consumption",
        json={**_body(), "item_id": "not-a-uuid"},
        headers=_headers(),
    )

    assert invalid_state.status_code == invalid_path.status_code == invalid_body.status_code == 422
    assert harness.repository.list_for_run(USER_A, RUN_A) == ()


def test_hidden_or_foreign_hierarchy_maps_to_opaque_404() -> None:
    harness = _harness(_HiddenTargetRepository())

    response = _post(harness)

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "consumption_target_not_found",
            "detail": "the plan consumption target was not found",
        }
    }


def test_get_returns_empty_and_never_leaks_another_users_entries() -> None:
    harness = _harness()
    path = f"/v1/plans/{RUN_A}/consumption"

    empty = harness.client.get(path, headers=_headers(USER_A))
    _post(harness, user_id=USER_A)
    foreign = harness.client.get(path, headers=_headers(USER_B))

    assert empty.status_code == foreign.status_code == 200
    assert empty.json() == foreign.json() == {"entries": []}
    assert len(harness.repository.list_for_run(USER_A, RUN_A)) == 1


def test_get_preserves_repository_order_original_ids_and_timestamps_without_mutation() -> None:
    harness = _harness()
    earlier = ConsumptionEntry(
        entry_id=UUID(int=51),
        user_id=USER_A,
        plan_run_id=RUN_A,
        plan_version_id=VERSION_A,
        item_id=ITEM_A,
        state=ConsumptionState.SKIPPED,
        client_event_id=EVENT_B,
        recorded_at=TIME_A,
    )
    later = ConsumptionEntry(
        entry_id=UUID(int=52),
        user_id=USER_A,
        plan_run_id=RUN_A,
        plan_version_id=VERSION_A,
        item_id=ITEM_A,
        state=ConsumptionState.EATEN,
        client_event_id=EVENT_A,
        recorded_at=TIME_A + timedelta(minutes=5),
    )
    harness.repository.save(later)
    harness.repository.save(earlier)

    before = harness.repository.list_for_run(USER_A, RUN_A)
    response = harness.client.get(
        f"/v1/plans/{RUN_A}/consumption",
        headers=_headers(),
    )
    after = harness.repository.list_for_run(USER_A, RUN_A)

    assert response.status_code == 200
    assert [entry["entry_id"] for entry in response.json()["entries"]] == [
        str(earlier.entry_id),
        str(later.entry_id),
    ]
    assert response.json()["entries"][0]["recorded_at"] == TIME_A.isoformat()
    assert before == after == (earlier, later)


def test_no_dsn_consumption_routes_fail_closed() -> None:
    settings = _settings()
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    client = TestClient(app)
    path = f"/v1/plans/{RUN_A}/consumption"

    posted = client.post(path, json=_body(), headers=_headers())
    listed = client.get(path, headers=_headers())

    assert posted.status_code == listed.status_code == 503
    assert posted.json()["error"]["code"] == "storage_unavailable"
    assert listed.json()["error"]["code"] == "storage_unavailable"


def test_consumption_logging_redacts_token_identity_and_request_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = _harness()
    token = _token()

    with caplog.at_level(logging.INFO, logger="nutrition_agent.consumption_api"):
        response = harness.client.post(
            f"/v1/plans/{RUN_A}/consumption",
            json=_body(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 201
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "subject_hash" in joined
    for sensitive in (token, str(USER_A), str(VERSION_A), str(ITEM_A), str(EVENT_A)):
        assert sensitive not in joined
