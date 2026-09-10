"""JWT verification for the health API (Supabase Auth tokens, ADR-016).

Two verification modes:
- HS256 via SUPABASE_JWT_SECRET (Supabase legacy JWT secret), or
- asymmetric (RS/ES) via the project JWKS at
  SUPABASE_URL/auth/v1/.well-known/jwks.json.

`aud` and `exp` are always enforced; the caller's identity is exclusively the
verified `sub` claim. Missing configuration or invalid tokens raise, and the
router maps that to 401/503 — authentication is never bypassed.
"""

from __future__ import annotations

import jwt
from jwt import PyJWKClient

from nutrition_agent.api.settings import HealthApiSettings


class InvalidToken(Exception):
    """Token missing, malformed, or failing verification."""


class AuthNotConfigured(Exception):
    """No verification material configured; refuse rather than bypass."""


class TokenVerifier:
    def __init__(self, settings: HealthApiSettings) -> None:
        self._settings = settings
        self._jwk_client: PyJWKClient | None = None
        if settings.supabase_url:
            self._jwk_client = PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")

    def verified_subject(self, authorization_header: str | None) -> str:
        header = authorization_header or ""
        if not header.startswith("Bearer "):
            raise InvalidToken("missing bearer token")
        token = header[len("Bearer ") :].strip()
        if not token:
            raise InvalidToken("missing bearer token")
        if not self._settings.jwt_secret and self._jwk_client is None:
            raise AuthNotConfigured("no JWT verification material configured")

        try:
            if self._jwk_client is not None:
                signing_key = self._jwk_client.get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256", "ES256"],
                    audience=self._settings.jwt_audience,
                )
            else:
                claims = jwt.decode(
                    token,
                    self._settings.jwt_secret,  # type: ignore[arg-type]
                    algorithms=["HS256"],
                    audience=self._settings.jwt_audience,
                )
        except jwt.PyJWTError as exc:
            raise InvalidToken(f"token verification failed: {exc}") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidToken("token has no sub claim")
        return subject
