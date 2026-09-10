from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "stacks-refresh.yml"


def test_public_workflow_runs_only_safe_fixture_checks() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "pytest backend/tests/unit/test_menu_parser.py" in text
    assert "secrets." not in text
    assert "--source live" not in text
    assert "STACKS_DATABASE_URL" not in text
    assert "upload-artifact" not in text


def test_public_workflow_has_no_automatic_institutional_scrape() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "schedule:" not in text
    assert "refresh_stacks" not in text
    assert "penn" not in text
