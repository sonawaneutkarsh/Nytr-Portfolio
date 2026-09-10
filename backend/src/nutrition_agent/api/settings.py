"""Environment-driven settings for the M5 health API (ADR-016).

Compliance posture mirrors config.py: safe/absent defaults. When auth or DB
configuration is missing the API degrades to 503 (retryable) rather than
bypassing authentication.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_AUDIENCE = "authenticated"


@dataclass(frozen=True)
class HealthApiSettings:
    database_url: str | None
    jwt_secret: str | None  # HS256 (Supabase legacy JWT secret)
    supabase_url: str | None  # enables JWKS (asymmetric) verification
    jwt_audience: str
    hevy_api_key: str | None = field(
        default=None,
        repr=False,
    )  # backend-only; never returned or persisted
    gemini_api_key: str | None = field(
        default=None,
        repr=False,
    )  # backend-only; never returned, logged, persisted, or sent to iOS
    gemini_model: str = "gemini-2.5-flash"
    open_food_facts_user_agent: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> HealthApiSettings:
        env = dict(os.environ if env is None else env)
        return cls(
            database_url=env.get("HEALTH_DATABASE_URL") or env.get("STACKS_DATABASE_URL"),
            jwt_secret=env.get("SUPABASE_JWT_SECRET"),
            supabase_url=(env.get("SUPABASE_URL") or "").rstrip("/") or None,
            jwt_audience=env.get("SUPABASE_JWT_AUDIENCE", DEFAULT_AUDIENCE),
            hevy_api_key=env.get("HEVY_API_KEY") or None,
            gemini_api_key=env.get("GEMINI_API_KEY") or None,
            gemini_model=env.get("GEMINI_MODEL", "gemini-2.5-flash"),
            open_food_facts_user_agent=env.get("OPEN_FOOD_FACTS_USER_AGENT") or None,
        )
