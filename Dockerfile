# M11A: minimal production container for the one-user personal backend.
# Deliberately no orchestrator features: a single uvicorn process, one env
# contract, and an unauthenticated /healthz liveness probe. No secrets are
# baked into the image; every runtime value arrives via environment variables.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/src ./backend/src
RUN pip install --no-cache-dir "./backend[db]" \
    && useradd --create-home --shell /usr/sbin/nologin appuser

USER appuser
EXPOSE 8080

# PORT is honored for platform-provided port injection; binds all interfaces.
CMD ["sh", "-c", "exec uvicorn nutrition_agent.api.app:create_health_app --factory --host 0.0.0.0 --port \"${PORT:-8080}\""]
