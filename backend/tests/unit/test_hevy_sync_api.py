"""Authenticated, secret-safe API contract for explicit bounded Hevy sync."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.hevy_sync import SyncHevyDetailedTrainingUseCase
from nutrition_agent.application.ports import (
    DetailedTrainingSourceError,
    DetailedTrainingSourceFailureKind,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryDetailedTrainingRepository,
    InMemoryHealthBodyMassRepository,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingSourceFetch,
)

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    def __init__(self) -> None:
        self.value = 100

    def new_id(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


class _Source:
    is_configured = True

    def load_initial(self) -> DetailedTrainingSourceFetch:
        return _empty_fetch()

    def load_changes(self, since: datetime) -> DetailedTrainingSourceFetch:
        del since
        return _empty_fetch()


class _FailingSource(_Source):
    def __init__(self, kind: DetailedTrainingSourceFailureKind) -> None:
        self._kind = kind

    def load_initial(self) -> DetailedTrainingSourceFetch:
        raise DetailedTrainingSourceError(self._kind, "sanitized provider failure")


class _UnconfiguredSource(_Source):
    is_configured = False


def _empty_fetch() -> DetailedTrainingSourceFetch:
    return DetailedTrainingSourceFetch(
        batch=DetailedTrainingImportBatch((), (), True),
        pages_fetched=1,
        has_more=False,
        source_event_watermark=NOW,
        logical_requests=1,
        attempts_made=1,
        retries=0,
        parser_version="hevy-public-api.v1",
        provider_version="hevy-api-provider.v1",
    )


def _token(subject: UUID = USER_A) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(subject),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _client(
    source: _Source | None,
    repository: InMemoryDetailedTrainingRepository | None = None,
) -> tuple[TestClient, InMemoryDetailedTrainingRepository]:
    settings = HealthApiSettings(None, SECRET, None, AUDIENCE)
    repository = repository or InMemoryDetailedTrainingRepository()
    sync = (
        SyncHevyDetailedTrainingUseCase(source, repository, _Clock(), _Ids())
        if source is not None
        else None
    )
    app = create_health_app(
        HealthApiDeps(
            settings,
            TokenVerifier(settings),
            HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock())),
            training_detail_sync_use_case=sync,
        )
    )
    return TestClient(app), repository


def _post(client: TestClient, subject: UUID = USER_A, json: object | None = None):
    return client.post(
        "/v1/training/hevy/sync",
        headers={"Authorization": f"Bearer {_token(subject)}"},
        json=json,
    )


def test_hevy_key_is_environment_only_and_excluded_from_settings_repr() -> None:
    settings = HealthApiSettings.from_env({"HEVY_API_KEY": "private-test-value"})

    assert settings.hevy_api_key == "private-test-value"
    assert "private-test-value" not in repr(settings)


def test_sync_requires_auth_and_storage() -> None:
    client, _ = _client(_Source())
    assert client.post("/v1/training/hevy/sync").status_code == 401

    unavailable, _ = _client(None)
    response = _post(unavailable)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"


def test_configured_sync_is_owner_scoped_and_returns_only_safe_facts() -> None:
    client, repository = _client(_Source())

    response = _post(
        client,
        USER_A,
        {"user_id": str(USER_B), "api_key": "must-be-ignored"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "synced",
        "mode": "bootstrap",
        "sessions_created": 0,
        "revisions_appended": 0,
        "deletions_recorded": 0,
        "events_replayed": 0,
        "tombstone_blocked": 0,
        "pages_fetched": 1,
        "has_more": False,
        "source_event_watermark": "2026-09-04T12:00:00+00:00",
        "logical_requests": 1,
        "attempts_made": 1,
        "retries": 0,
        "checkpoint_advanced": True,
    }
    checkpoint = repository.sync_checkpoints[0]
    assert checkpoint.user_id == USER_A
    assert "api_key" not in response.text and "must-be-ignored" not in response.text


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    (
        (DetailedTrainingSourceFailureKind.NOT_CONFIGURED, 503, "hevy_not_configured"),
        (DetailedTrainingSourceFailureKind.UNAUTHORIZED, 502, "hevy_unauthorized"),
        (DetailedTrainingSourceFailureKind.FORBIDDEN, 502, "hevy_forbidden"),
        (DetailedTrainingSourceFailureKind.RATE_LIMITED, 429, "hevy_rate_limited"),
        (DetailedTrainingSourceFailureKind.TIMEOUT, 503, "hevy_timeout"),
        (
            DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE,
            503,
            "hevy_unavailable",
        ),
        (
            DetailedTrainingSourceFailureKind.MALFORMED_RESPONSE,
            502,
            "hevy_malformed_response",
        ),
        (
            DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION,
            502,
            "hevy_incomplete_pagination",
        ),
    ),
)
def test_provider_failures_have_typed_secret_safe_http_contracts(
    kind: DetailedTrainingSourceFailureKind,
    status: int,
    code: str,
) -> None:
    source: _Source = (
        _UnconfiguredSource()
        if kind is DetailedTrainingSourceFailureKind.NOT_CONFIGURED
        else _FailingSource(kind)
    )
    client, repository = _client(source)

    response = _post(client)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "sanitized provider failure" not in response.text
    assert repository.sync_checkpoints == []
