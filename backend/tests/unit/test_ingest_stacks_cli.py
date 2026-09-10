"""M8 one-page CLI authority and network-isolation tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.config import Settings
from nutrition_agent.db.in_memory_repos import InMemoryRunRepository
from nutrition_agent.db.sql_repos import SqlRunRepository
from nutrition_agent.domain.stacks.ingestion import IngestionRunReport, RunStatus
from nutrition_agent.infrastructure.compliance_gate import IngestionMode
from nutrition_agent.scripts import ingest_stacks
from tests.conftest import FIXTURE_DIR


def _settings(
    tmp_path: Path,
    *,
    mode: IngestionMode = IngestionMode.BLOCKED,
    user_agent: str = "nutrition-agent-personal/0.8 (manual-run; contact: owner)",
    dsn: str | None = None,
) -> Settings:
    return Settings(
        ingestion_mode=mode,
        snapshot_root=tmp_path,
        base_url="https://institutional-menu.example.invalid",
        campus_id=50,
        user_agent=user_agent,
        min_request_interval_seconds=10,
        attempt_budget=150,
        max_attempts=2,
        request_timeout_seconds=20,
        max_items_per_page=125,
        database_url=dsn,
    )


def test_environment_defaults_are_blocked_and_transport_bounded() -> None:
    settings = Settings.from_env({})

    assert settings.ingestion_mode is IngestionMode.BLOCKED
    assert settings.min_request_interval_seconds == 10
    assert settings.max_attempts == 2
    assert settings.request_timeout_seconds == 20
    assert settings.attempt_budget == 150
    assert settings.max_items_per_page == 125
    assert ingest_stacks._has_real_owner_contact(settings.user_agent) is False


def test_fixture_is_safe_default_and_constructs_no_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class ForbiddenTransport:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("fixture mode attempted to construct network transport")

    monkeypatch.setattr(ingest_stacks, "HttpxTransport", ForbiddenTransport)
    monkeypatch.setattr(
        ingest_stacks.Settings,
        "from_env",
        classmethod(lambda cls: _settings(tmp_path)),
    )

    result = ingest_stacks.main(
        [
            "--service-date",
            "2026-08-21",
            "--meal-period",
            "Breakfast",
            "--fixtures",
            str(FIXTURE_DIR),
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert '"source": "fixture"' in captured.out
    assert '"authoritative": false' in captured.out
    assert "no authoritative SQL state" in captured.err


@pytest.mark.parametrize("status", [RunStatus.PARTIAL_FAILURE, RunStatus.FAILED])
def test_unsuccessful_terminal_report_returns_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: RunStatus,
) -> None:
    class ForbiddenTransport:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("exit-code test attempted to construct network transport")

    report = IngestionRunReport(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        status=status,
        stats={},
        quarantines_by_code={},
    )
    monkeypatch.setattr(ingest_stacks, "HttpxTransport", ForbiddenTransport)
    monkeypatch.setattr(
        ingest_stacks.Settings,
        "from_env",
        classmethod(lambda cls: _settings(tmp_path)),
    )
    monkeypatch.setattr(
        ingest_stacks.IngestStacksUseCase,
        "execute",
        lambda self, command: report,
    )

    result = ingest_stacks.main(
        [
            "--service-date",
            "2026-08-21",
            "--meal-period",
            "Breakfast",
            "--fixtures",
            str(FIXTURE_DIR),
        ]
    )

    assert result == 1


@pytest.mark.parametrize(
    ("mode", "user_agent", "dsn", "expected"),
    [
        (
            IngestionMode.BLOCKED,
            "nutrition-agent/0.8 (contact: demo@example.org)",
            "demo-dsn",
            "STACKS_INGESTION_MODE=manual",
        ),
        (
            IngestionMode.MANUAL,
            "nutrition-agent/0.8 (contact: owner)",
            "demo-dsn",
            "real owner email",
        ),
        (
            IngestionMode.MANUAL,
            "nutrition-agent/0.8 (contact: demo@example.org)",
            None,
            "STACKS_DATABASE_URL",
        ),
    ],
)
def test_live_authority_validation_fails_before_dependency_or_network_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: IngestionMode,
    user_agent: str,
    dsn: str | None,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        ingest_stacks.Settings,
        "from_env",
        classmethod(lambda cls: _settings(tmp_path, mode=mode, user_agent=user_agent, dsn=dsn)),
    )
    monkeypatch.setattr(
        ingest_stacks,
        "_build_deps",
        lambda *args, **kwargs: pytest.fail("dependencies built before validation"),
    )

    with pytest.raises(SystemExit) as exc_info:
        ingest_stacks.main(
            [
                "--source",
                "live",
                "--service-date",
                "2026-08-27",
                "--meal-period",
                "Lunch",
                "--persist",
            ]
        )
    assert exc_info.value.code == 2
    assert expected in capsys.readouterr().err


def test_live_requires_explicit_authority_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        ingest_stacks.Settings,
        "from_env",
        classmethod(
            lambda cls: _settings(
                tmp_path,
                mode=IngestionMode.MANUAL,
                user_agent="nutrition-agent/0.8 (contact: demo@example.org)",
            )
        ),
    )
    with pytest.raises(SystemExit):
        ingest_stacks.main(
            [
                "--source",
                "live",
                "--service-date",
                "2026-08-27",
                "--meal-period",
                "Lunch",
            ]
        )
    assert "explicit --persist or --dry-run" in capsys.readouterr().err


def test_dependency_wiring_distinguishes_dry_run_from_sql_authority(tmp_path: Path) -> None:
    dry = ingest_stacks._build_deps(
        _settings(tmp_path),
        source_name="fixture",
        fixture_dir=FIXTURE_DIR,
        persist=False,
    )
    durable = ingest_stacks._build_deps(
        _settings(tmp_path, dsn="demo-dsn"),
        source_name="fixture",
        fixture_dir=FIXTURE_DIR,
        persist=True,
    )

    assert isinstance(dry.runs, InMemoryRunRepository)
    assert isinstance(durable.runs, SqlRunRepository)


def test_cli_shape_cannot_expand_to_multiple_periods() -> None:
    with pytest.raises(SystemExit):
        ingest_stacks._parser().parse_args(
            [
                "--service-date",
                "2026-08-27",
                "--meal-period",
                "Lunch,Dinner",
            ]
        )
