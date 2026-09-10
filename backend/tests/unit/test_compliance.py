from __future__ import annotations

import pytest

from nutrition_agent.domain.stacks.ingestion import ErrorCode, RunStatus
from nutrition_agent.infrastructure.compliance_gate import (
    BlockedByPolicy,
    ComplianceGate,
    IngestionMode,
)
from nutrition_agent.infrastructure.http_transport import HttpxTransport
from tests.conftest import make_use_case


def test_blocked_mode_is_default_and_records_policy_run(lunch_command) -> None:
    class SpySource:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_menu_page(self, service_date, meal_period):  # pragma: no cover
            self.calls += 1
            raise AssertionError("network fetch attempted in blocked mode")

        def fetch_label(self, mid_instance):  # pragma: no cover
            self.calls += 1
            raise AssertionError("network fetch attempted in blocked mode")

    spy = SpySource()
    use_case, deps = make_use_case(mode=IngestionMode.BLOCKED, source=spy)

    report = use_case.execute(lunch_command)

    assert spy.calls == 0
    assert report.status == RunStatus.BLOCKED_BY_POLICY
    assert report.quarantines_by_code.get(ErrorCode.TRANSPORT_BLOCKED_BY_POLICY.value) == 1
    stored = deps.runs.runs[report.run_id]
    assert stored.status == RunStatus.BLOCKED_BY_POLICY


def test_compliance_gate_rejects_blocked_mode() -> None:
    gate = ComplianceGate(IngestionMode.BLOCKED)
    with pytest.raises(BlockedByPolicy) as excinfo:
        gate.ensure_fetch_allowed("institutional_menu")
    assert "ADR-010" in str(excinfo.value)


def test_scheduled_mode_is_an_explicit_allowed_gate_state() -> None:
    ComplianceGate(IngestionMode.SCHEDULED).ensure_fetch_allowed("institutional_menu")


def test_transport_checks_gate_before_any_network_attempt() -> None:
    transport = HttpxTransport(
        ComplianceGate(IngestionMode.BLOCKED),
        "institutional_menu",
        user_agent="test",
        min_interval_seconds=0,
    )
    from nutrition_agent.infrastructure.http_transport import HttpRequest

    with pytest.raises(BlockedByPolicy):
        transport.fetch(HttpRequest(url="http://example.invalid"))
    assert transport.requests_made == 0
