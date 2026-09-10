from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from nutrition_agent.application.ports import IngestCommand
from nutrition_agent.application.stacks_refresh import StacksRefreshScope
from nutrition_agent.config import Settings
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.domain.stacks.ingestion import IngestionRunReport, RunStatus
from nutrition_agent.infrastructure.compliance_gate import IngestionMode
from nutrition_agent.infrastructure.http_transport import TransportMetrics
from nutrition_agent.scripts import refresh_stacks
from tests.conftest import FIXTURE_DIR


def _settings(
    tmp_path: Path,
    *,
    mode: IngestionMode = IngestionMode.BLOCKED,
    user_agent: str = "nutrition-agent/0.11 (contact: owner)",
    dsn: str | None = None,
    base_url: str = "https://institutional-menu.example.invalid",
    attempt_budget: int = 150,
) -> Settings:
    return Settings(
        ingestion_mode=mode,
        snapshot_root=tmp_path,
        base_url=base_url,
        campus_id=50,
        user_agent=user_agent,
        min_request_interval_seconds=10,
        attempt_budget=attempt_budget,
        max_attempts=2,
        request_timeout_seconds=20,
        max_items_per_page=125,
        database_url=dsn,
    )


def test_fixture_refresh_is_network_isolated_and_bounded_to_one_page(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        refresh_stacks.Settings,
        "from_env",
        classmethod(lambda cls: _settings(tmp_path)),
    )

    result = refresh_stacks.main(
        [
            "--source",
            "fixture",
            "--dry-run",
            "--fixture-as-of-date",
            "2026-08-21",
            "--fixtures",
            str(FIXTURE_DIR),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert '"service_date": "2026-08-21"' in captured.out
    assert '"meal_period": "Breakfast"' in captured.out
    assert '"authoritative": false' in captured.out
    assert "DRY RUN" in captured.err


@pytest.mark.parametrize(
    ("mode", "user_agent", "dsn", "base_url", "expected"),
    [
        (
            IngestionMode.BLOCKED,
            "nutrition-agent/0.11 (contact: demo@example.org)",
            "demo-dsn",
            "https://institutional-menu.example.invalid",
            "STACKS_INGESTION_MODE=scheduled",
        ),
        (
            IngestionMode.SCHEDULED,
            "nutrition-agent/0.11 (contact: owner)",
            "demo-dsn",
            "https://institutional-menu.example.invalid",
            "genuine owner-contact",
        ),
        (
            IngestionMode.SCHEDULED,
            "nutrition-agent/0.11 (contact: demo@example.org)",
            None,
            "https://institutional-menu.example.invalid",
            "STACKS_DATABASE_URL",
        ),
        (
            IngestionMode.SCHEDULED,
            "nutrition-agent/0.11 (contact: demo@example.org)",
            "demo-dsn",
            "https://source.example",
            "official Stacks source host",
        ),
    ],
)
def test_live_preflight_fails_before_source_or_database_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: IngestionMode,
    user_agent: str,
    dsn: str | None,
    base_url: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        refresh_stacks.Settings,
        "from_env",
        classmethod(
            lambda cls: _settings(
                tmp_path,
                mode=mode,
                user_agent=user_agent,
                dsn=dsn,
                base_url=base_url,
            )
        ),
    )
    monkeypatch.setattr(
        refresh_stacks,
        "SqlStacksRefreshLease",
        lambda dsn: pytest.fail("database lease constructed before preflight"),
    )
    monkeypatch.setattr(
        refresh_stacks,
        "_build_deps",
        lambda *args, **kwargs: pytest.fail("source dependencies built before preflight"),
    )

    with pytest.raises(SystemExit) as exc_info:
        refresh_stacks.main(["--source", "live", "--refresh-mode", "full", "--persist"])

    assert exc_info.value.code == 2
    assert expected in capsys.readouterr().err


def test_live_rejects_fixture_date_and_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        refresh_stacks.Settings,
        "from_env",
        classmethod(
            lambda cls: _settings(
                tmp_path,
                mode=IngestionMode.SCHEDULED,
                user_agent="nutrition-agent/0.11 (contact: demo@example.org)",
                dsn="demo-dsn",
            )
        ),
    )
    with pytest.raises(SystemExit):
        refresh_stacks.main(
            [
                "--source",
                "live",
                "--refresh-mode",
                "full",
                "--dry-run",
                "--fixture-as-of-date",
                "2026-08-30",
            ]
        )
    assert "explicit --persist" in capsys.readouterr().err


def test_live_mode_must_be_explicit_before_source_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        refresh_stacks.Settings,
        "from_env",
        classmethod(
            lambda cls: dataclasses.replace(
                _scheduled_settings(tmp_path),
                attempt_budget=refresh_stacks.MAX_CANARY_ATTEMPTS,
            )
        ),
    )
    monkeypatch.setattr(
        refresh_stacks,
        "_build_deps",
        lambda *args, **kwargs: pytest.fail("source constructed before mode validation"),
    )

    with pytest.raises(SystemExit):
        refresh_stacks.main(["--source", "live", "--persist"])

    assert "explicit --refresh-mode canary|bootstrap|full" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("scope", "limit"),
    (
        (StacksRefreshScope.CANARY, refresh_stacks.MAX_CANARY_ATTEMPTS),
        (StacksRefreshScope.BOOTSTRAP, refresh_stacks.MAX_BOOTSTRAP_ATTEMPTS),
        (StacksRefreshScope.FULL, refresh_stacks.MAX_SCHEDULED_ATTEMPTS),
    ),
)
def test_live_attempt_budget_is_bounded_by_refresh_scope(
    tmp_path: Path,
    scope: StacksRefreshScope,
    limit: int,
) -> None:
    parser = refresh_stacks._parser()
    args = parser.parse_args(["--source", "live", "--refresh-mode", scope.value, "--persist"])
    accepted = _settings(
        tmp_path,
        mode=IngestionMode.SCHEDULED,
        user_agent="nutrition-agent/0.11 (contact: demo@example.org)",
        dsn="demo-dsn",
        attempt_budget=limit,
    )

    refresh_stacks._validate_args(parser, args, accepted)

    rejected = dataclasses.replace(
        accepted,
        attempt_budget=limit + 1,
    )
    with pytest.raises(SystemExit):
        refresh_stacks._validate_args(parser, args, rejected)


@pytest.mark.parametrize(
    ("scope", "expected_cap"),
    (
        (StacksRefreshScope.CANARY, 5),
        (StacksRefreshScope.BOOTSTRAP, 125),
        (StacksRefreshScope.FULL, None),
    ),
)
def test_live_execute_injects_only_the_selected_scope_label_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scope: StacksRefreshScope,
    expected_cap: int | None,
) -> None:
    captured_caps: list[int | None] = []
    today = date(2026, 8, 30)
    advertised = tuple(today + timedelta(days=offset) for offset in range(7))

    class FakeLazyIngester:
        metrics = None

        def __init__(self, **kwargs: object) -> None:
            cap = kwargs.get("max_unique_label_fetches")
            assert cap is None or isinstance(cap, int)
            captured_caps.append(cap)

        def execute(self, command: IngestCommand) -> IngestionRunReport:
            return IngestionRunReport(
                run_id=UUID(int=command.service_date.toordinal()),
                status=RunStatus.PERSISTED,
                stats={},
                quarantines_by_code={},
                advertised_dates=advertised,
            )

    monkeypatch.setattr(refresh_stacks, "_LazyIngester", FakeLazyIngester)
    args = SimpleNamespace(
        source="live",
        refresh_mode=scope.value,
        persist=True,
        fixtures=None,
    )

    refresh_stacks._execute(
        args=args,
        settings=_settings(tmp_path),
        coverage=refresh_stacks._EmptyCoverageRepository(),
        clock=refresh_stacks.RequestedDateClock(today),
    )

    assert captured_caps == [expected_cap]


def test_lazy_ingester_reuses_one_stack_across_the_daily_page_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    builds: list[object] = []
    constructions: list[object] = []
    commands: list[IngestCommand] = []

    def build_deps(*args: object, **kwargs: object) -> object:
        deps = SimpleNamespace(source=object())
        builds.append(deps)
        return deps

    class FakeUseCase:
        def __init__(self, deps: object) -> None:
            constructions.append(deps)

        def execute(self, command: IngestCommand) -> IngestionRunReport:
            commands.append(command)
            return IngestionRunReport(
                run_id=UUID(int=len(commands)),
                status=RunStatus.PERSISTED,
                stats={},
                quarantines_by_code={},
            )

    monkeypatch.setattr(refresh_stacks, "_build_deps", build_deps)
    monkeypatch.setattr(refresh_stacks, "IngestStacksUseCase", FakeUseCase)
    ingester = refresh_stacks._LazyIngester(
        settings=_settings(tmp_path),
        source_name="fixture",
        fixture_dir=FIXTURE_DIR,
        persist=False,
        clock=refresh_stacks.SystemClock(),
    )

    ingester.execute(IngestCommand(date(2026, 8, 30), (MealPeriod.BREAKFAST,)))
    ingester.execute(IngestCommand(date(2026, 8, 30), (MealPeriod.LUNCH,)))

    assert len(builds) == 1
    assert constructions == builds
    assert [command.meal_periods for command in commands] == [
        (MealPeriod.BREAKFAST,),
        (MealPeriod.LUNCH,),
    ]


class _Lease:
    def __init__(self, dsn: str, *, acquired: bool) -> None:
        self.acquired = acquired

    def __enter__(self) -> _Lease:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _scheduled_settings(tmp_path: Path) -> Settings:
    return _settings(
        tmp_path,
        mode=IngestionMode.SCHEDULED,
        user_agent="nutrition-agent/0.11 (contact: demo@example.org)",
        dsn="demo-dsn",
    )


def test_overlap_is_a_visible_noop_and_constructs_no_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        refresh_stacks.Settings,
        "from_env",
        classmethod(
            lambda cls: dataclasses.replace(
                _scheduled_settings(tmp_path),
                attempt_budget=refresh_stacks.MAX_CANARY_ATTEMPTS,
            )
        ),
    )
    monkeypatch.setattr(
        refresh_stacks,
        "SqlStacksRefreshLease",
        lambda dsn: _Lease(dsn, acquired=False),
    )
    monkeypatch.setattr(
        refresh_stacks,
        "_build_deps",
        lambda *args, **kwargs: pytest.fail("overlap constructed a source"),
    )

    result = refresh_stacks.main(["--source", "live", "--refresh-mode", "canary", "--persist"])

    assert result == 0
    assert '"status": "overlap_skipped"' in capsys.readouterr().out


def test_structured_transport_metrics_contain_no_request_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from nutrition_agent.application.stacks_refresh import StacksRefreshResult

    refresh_stacks._print_result(
        StacksRefreshResult(
            status=refresh_stacks.StacksRefreshStatus.UP_TO_DATE,
            pages=(),
        ),
        source="live",
        scope=StacksRefreshScope.CANARY,
        authoritative=True,
        metrics=TransportMetrics(
            attempts_made=3,
            retry_causes=("http_503",),
            observed_min_spacing_seconds=10,
            attempt_budget=150,
            configured_min_interval_seconds=10,
        ),
    )

    output = capsys.readouterr().out
    assert '"attempts": 3' in output
    assert '"retries": 1' in output
    assert '"retry_causes": [' in output
    assert '"http_503"' in output
    assert '"refresh_mode": "canary"' in output
    assert '"menu_requests": 0' in output
    assert '"unique_label_requests": 0' in output
    assert '"accepted_pages": 0' in output
    assert '"stop_reason": null' in output
    assert "access_token" not in output
    assert "User-Agent" not in output
    assert "postgresql://" not in output
