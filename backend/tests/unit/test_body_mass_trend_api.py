"""Offline API contract tests for authenticated M9 body-mass trend reads."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.health.entities import BodyMassSample, SyncBatch
from nutrition_agent.domain.health.trend import BODY_MASS_TREND_ALGORITHM_VERSION

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
USER_B = UUID("00000000-0000-0000-0000-0000000000b2")
AS_OF = date(2026, 8, 28)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 28, 12, tzinfo=UTC)


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


def _harness() -> tuple[TestClient, InMemoryHealthBodyMassRepository]:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    repo = InMemoryHealthBodyMassRepository()
    sync = HealthBodyMassSyncUseCase(HealthSyncDeps(repo, _Clock()))
    trend = BodyMassTrendUseCase(repo)
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), sync, trend))
    return TestClient(app), repo


def _sample(sample_id: int, day: date, value: str = "70") -> BodyMassSample:
    measured_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return BodyMassSample(
        sample_uuid=UUID(int=sample_id),
        value_kg=Decimal(value),
        sample_start=measured_at,
        sample_end=measured_at,
    )


def _seed(
    repo: InMemoryHealthBodyMassRepository,
    user_id: UUID,
    samples: tuple[BodyMassSample, ...],
) -> None:
    repo.apply_batch(user_id, SyncBatch(UUID(int=0xF), samples, ()))


def _get(
    client: TestClient,
    *,
    user_id: UUID = USER_A,
    as_of_date: str = "2026-08-28",
    timezone: str = "UTC",
):
    return client.get(
        "/v1/health/body-mass/trend",
        params={"as_of_date": as_of_date, "timezone": timezone},
        headers={"Authorization": f"Bearer {_token(user_id)}"},
    )


def _ready_samples() -> tuple[BodyMassSample, ...]:
    return tuple(
        _sample(index + 1, AS_OF + timedelta(days=offset), value)
        for index, (offset, value) in enumerate(
            zip(
                (-20, -18, -16, -14, -12, -6, 0),
                ("68.001", "68.002", "68.003", "68.004", "68.005", "68.006", "68.007"),
                strict=True,
            )
        )
    )


def test_trend_requires_existing_bearer_authentication() -> None:
    client, _ = _harness()
    missing = client.get(
        "/v1/health/body-mass/trend",
        params={"as_of_date": "2026-08-28", "timezone": "UTC"},
    )
    garbage = client.get(
        "/v1/health/body-mass/trend",
        params={"as_of_date": "2026-08-28", "timezone": "UTC"},
        headers={"Authorization": "Bearer garbage"},
    )
    assert missing.status_code == garbage.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_token"


def test_trend_fails_closed_when_durable_history_is_not_configured() -> None:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    repo = InMemoryHealthBodyMassRepository()
    sync = HealthBodyMassSyncUseCase(HealthSyncDeps(repo, _Clock()))
    client = TestClient(create_health_app(HealthApiDeps(settings, TokenVerifier(settings), sync)))

    response = _get(client)

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "storage_unavailable", "detail": "retry later"}}


def test_no_data_is_success_with_explicit_null_optionals() -> None:
    client, _ = _harness()
    response = _get(client)
    assert response.status_code == 200
    assert response.json() == {
        "status": "no_data",
        "as_of_date": "2026-08-28",
        "timezone": "UTC",
        "algorithm_version": BODY_MASS_TREND_ALGORITHM_VERSION,
        "input_digest": response.json()["input_digest"],
        "represented_day_count": 0,
        "coverage_span_days": 0,
        "first_measurement_date": None,
        "last_measurement_date": None,
        "latest_measurement_date": None,
        "latest_measurement_age_days": None,
        "trailing_7d_average_kg": None,
        "weekly_rate_kg": None,
    }
    assert len(response.json()["input_digest"]) == 64


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ((_sample(1, AS_OF),), "insufficient"),
        ((_sample(1, AS_OF - timedelta(days=8)),), "stale"),
        (_ready_samples(), "ready"),
    ],
)
def test_non_error_statuses_are_exact_success_states(
    samples: tuple[BodyMassSample, ...], expected: str
) -> None:
    client, repo = _harness()
    _seed(repo, USER_A, samples)
    response = _get(client)
    assert response.status_code == 200
    assert response.json()["status"] == expected


def test_query_validation_and_url_encoded_iana_timezone() -> None:
    client, _ = _harness()
    encoded = _get(client, timezone="Etc/GMT+5")
    invalid_timezone = _get(client, timezone="Mars/Olympus_Mons")
    malformed_date = _get(client, as_of_date="not-a-date")

    assert encoded.status_code == 200
    assert encoded.json()["timezone"] == "Etc/GMT+5"
    assert invalid_timezone.status_code == 400
    assert invalid_timezone.json() == {
        "error": {"code": "invalid_request", "detail": "timezone is not recognized"}
    }
    assert malformed_date.status_code == 422


def test_decimal_strings_digest_version_and_ready_nullable_average_are_lossless() -> None:
    client, repo = _harness()
    samples = _ready_samples()
    _seed(repo, USER_A, samples)
    expected = BodyMassTrendUseCase(repo).execute(user_id=USER_A, as_of_date=AS_OF, timezone="UTC")

    body = _get(client).json()

    assert body["status"] == "ready"
    assert body["algorithm_version"] == BODY_MASS_TREND_ALGORITHM_VERSION
    assert body["input_digest"] == expected.input_digest
    assert body["weekly_rate_kg"] == str(expected.weekly_rate_kg)
    assert isinstance(body["weekly_rate_kg"], str)
    assert body["trailing_7d_average_kg"] is None  # two recent days is valid READY
    assert body["first_measurement_date"] == "2026-08-08"
    assert body["latest_measurement_date"] == "2026-08-28"


def test_daily_even_median_reaches_api_without_rounding() -> None:
    client, repo = _harness()
    recent_days = (AS_OF - timedelta(days=2), AS_OF - timedelta(days=1), AS_OF)
    samples = (
        _sample(1, recent_days[0], "68.001"),
        _sample(2, recent_days[0], "68.002"),
        _sample(3, recent_days[1], "68.003"),
        _sample(4, recent_days[2], "68.004"),
    )
    _seed(repo, USER_A, samples)
    expected = BodyMassTrendUseCase(repo).execute(user_id=USER_A, as_of_date=AS_OF, timezone="UTC")
    body = _get(client).json()
    assert body["trailing_7d_average_kg"] == str(expected.trailing_7d_average_kg)
    assert body["trailing_7d_average_kg"] == "68.00283333333333333333333333"


def test_user_b_samples_do_not_influence_user_a_response() -> None:
    client, repo = _harness()
    _seed(repo, USER_A, (_sample(1, AS_OF, "70"),))
    _seed(repo, USER_B, _ready_samples())

    a = _get(client, user_id=USER_A).json()
    b = _get(client, user_id=USER_B).json()

    assert a["status"] == "insufficient"
    assert a["represented_day_count"] == 1
    assert b["status"] == "ready"
    assert b["represented_day_count"] == 7


def test_get_is_repeatable_and_does_not_write_or_log_health_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, repo = _harness()
    _seed(repo, USER_A, (_sample(1, AS_OF, "72.000"),))
    before = dict(repo.rows)

    with caplog.at_level(logging.INFO, logger="nutrition_agent.health_api"):
        first = _get(client)
        second = _get(client)

    assert first.json() == second.json()
    assert repo.rows == before
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "72.000" not in joined
