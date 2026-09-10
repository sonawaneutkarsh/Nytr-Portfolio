"""Environment-driven settings. Compliance-safe defaults (ADR-010/ADR-013)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nutrition_agent.infrastructure.compliance_gate import IngestionMode


@dataclass(frozen=True)
class Settings:
    ingestion_mode: IngestionMode
    snapshot_root: Path
    base_url: str
    campus_id: int
    user_agent: str
    min_request_interval_seconds: float
    attempt_budget: int
    max_attempts: int
    request_timeout_seconds: float
    max_items_per_page: int
    database_url: str | None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        mode_raw = env.get("STACKS_INGESTION_MODE", IngestionMode.BLOCKED.value)
        mode = IngestionMode(mode_raw)
        return cls(
            ingestion_mode=mode,
            snapshot_root=Path(env.get("STACKS_SNAPSHOT_ROOT", "./snapshots")),
            base_url=env.get("STACKS_BASE_URL", "https://institutional-menu.example.invalid"),
            campus_id=int(env.get("STACKS_CAMPUS_ID", "50")),
            user_agent=env.get(
                "STACKS_USER_AGENT",
                "nutrition-agent-personal/0.8 (manual-run; contact: owner)",
            ),
            min_request_interval_seconds=float(env.get("STACKS_MIN_REQUEST_INTERVAL", "10")),
            attempt_budget=int(env.get("STACKS_ATTEMPT_BUDGET", "150")),
            max_attempts=int(env.get("STACKS_MAX_ATTEMPTS", "2")),
            request_timeout_seconds=float(env.get("STACKS_REQUEST_TIMEOUT", "20")),
            max_items_per_page=int(env.get("STACKS_MAX_ITEMS_PER_PAGE", "125")),
            database_url=env.get("STACKS_DATABASE_URL"),
        )
