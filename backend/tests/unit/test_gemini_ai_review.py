"""Gemini transport, schema, refusal, and cost-boundary tests."""

from __future__ import annotations

import json

import httpx
import pytest

from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.ai_review import AIReviewProviderError, AIReviewProviderFailure
from nutrition_agent.infrastructure.gemini_ai_review import GeminiAIReviewProvider
from tests.unit.test_ai_review import _snapshot


def _envelope(document: object) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(document)}]}}]}


def test_gemini_configuration_is_server_side_optional_and_redacted_from_repr() -> None:
    defaults = HealthApiSettings.from_env({})
    assert defaults.gemini_model == "gemini-2.5-flash"
    assert defaults.gemini_ai_review_enabled is False

    settings = HealthApiSettings.from_env(
        {
            "GEMINI_API_KEY": "never-log-this-test-value",
            "GEMINI_MODEL": "gemini-test-model",
            "GEMINI_AI_REVIEW_ENABLED": "true",
        }
    )

    assert settings.gemini_api_key == "never-log-this-test-value"
    assert settings.gemini_model == "gemini-test-model"
    assert settings.gemini_ai_review_enabled is True
    assert "never-log-this-test-value" not in repr(settings)


def test_valid_structured_request_is_single_bounded_call_without_raw_evidence() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/v1beta/models/gemini-2.5-flash:generateContent")
        assert request.headers["x-goog-api-key"] == "secret-key"
        body = json.loads(request.content)
        serialized = json.dumps(body)
        assert "IGNORE ALL INSTRUCTIONS" not in serialized
        assert body["generationConfig"]["maxOutputTokens"] == 400
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        schema = body["generationConfig"]["responseJsonSchema"]
        assert "responseFormat" not in body["generationConfig"]
        assert schema["required"] == [
            "summary",
            "attention_items",
            "evidence_notes",
            "limitations",
        ]
        assert set(schema["properties"]) == set(schema["required"])
        assert schema["additionalProperties"] is False
        schema_text = json.dumps(schema)
        assert "minLength" not in schema_text
        assert "maxLength" not in schema_text
        supported_keywords = {
            "type",
            "additionalProperties",
            "properties",
            "required",
            "items",
            "minItems",
            "maxItems",
        }

        def assert_supported_keywords(value: object) -> None:
            if not isinstance(value, dict):
                return
            for key, nested in value.items():
                assert key in supported_keywords
                if key == "properties":
                    assert isinstance(nested, dict)
                    for property_schema in nested.values():
                        assert_supported_keywords(property_schema)
                elif key == "items":
                    assert_supported_keywords(nested)

        assert_supported_keywords(schema)
        return httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "Recorded evidence is incomplete.",
                    "attention_items": ["Weight evidence is stale."],
                    "evidence_notes": ["Only recorded food is represented."],
                    "limitations": ["This is a non-authoritative explanation."],
                }
            ),
        )

    provider = GeminiAIReviewProvider(
        "secret-key",
        enabled=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.generate(_snapshot())
    assert result.summary == "Recorded evidence is incomplete."
    assert calls == 1


@pytest.mark.parametrize(
    ("status", "failure"),
    (
        (401, AIReviewProviderFailure.UNAVAILABLE),
        (403, AIReviewProviderFailure.UNAVAILABLE),
        (404, AIReviewProviderFailure.UNAVAILABLE),
        (429, AIReviewProviderFailure.RATE_LIMITED),
        (503, AIReviewProviderFailure.UNAVAILABLE),
    ),
)
def test_http_failures_are_typed(status: int, failure: AIReviewProviderFailure) -> None:
    provider = GeminiAIReviewProvider(
        "key",
        enabled=True,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private"))
        ),
    )
    with pytest.raises(AIReviewProviderError) as caught:
        provider.generate(_snapshot())
    assert caught.value.failure is failure


def test_timeout_is_typed_and_never_retried() -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    provider = GeminiAIReviewProvider(
        "key",
        enabled=True,
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    with pytest.raises(AIReviewProviderError) as caught:
        provider.generate(_snapshot())
    assert caught.value.failure is AIReviewProviderFailure.TIMEOUT
    assert calls == 1


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=_envelope({"summary": "Missing fields"})),
        httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "",
                    "attention_items": [],
                    "evidence_notes": [],
                    "limitations": ["Evidence is bounded."],
                }
            ),
        ),
        httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "A" * 321,
                    "attention_items": [],
                    "evidence_notes": [],
                    "limitations": ["Evidence is bounded."],
                }
            ),
        ),
        httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "Evidence is incomplete.",
                    "attention_items": ["Review this."] * 5,
                    "evidence_notes": [],
                    "limitations": ["Evidence is bounded."],
                }
            ),
        ),
        httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "Evidence is incomplete.",
                    "attention_items": ["Review this.", 1],
                    "evidence_notes": [],
                    "limitations": ["Evidence is bounded."],
                }
            ),
        ),
        httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "Evidence is incomplete.",
                    "attention_items": [],
                    "evidence_notes": [],
                    "limitations": ["Evidence is bounded."],
                    "unexpected": "field",
                }
            ),
        ),
        httpx.Response(
            200,
            json=_envelope(
                {
                    "summary": "Unsupported claim of 500 calories.",
                    "attention_items": [],
                    "evidence_notes": [],
                    "limitations": ["Evidence is bounded."],
                }
            ),
        ),
        httpx.Response(200, content=b"x" * 12_001),
    ),
)
def test_malformed_unexpected_numeric_and_oversized_outputs_are_invalid(
    response: httpx.Response,
) -> None:
    provider = GeminiAIReviewProvider(
        "key",
        enabled=True,
        client=httpx.Client(transport=httpx.MockTransport(lambda _: response)),
    )
    with pytest.raises(AIReviewProviderError) as caught:
        provider.generate(_snapshot())
    assert caught.value.failure is AIReviewProviderFailure.INVALID_RESPONSE


@pytest.mark.parametrize("key", (None, "", "   "))
def test_missing_key_and_safety_refusal_are_distinct(key: str | None) -> None:
    with pytest.raises(AIReviewProviderError) as missing:
        GeminiAIReviewProvider(key, enabled=True).generate(_snapshot())
    assert missing.value.failure is AIReviewProviderFailure.NOT_CONFIGURED

    provider = GeminiAIReviewProvider(
        "key",
        enabled=True,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
            )
        ),
    )
    with pytest.raises(AIReviewProviderError) as refused:
        provider.generate(_snapshot())
    assert refused.value.failure is AIReviewProviderFailure.REFUSED


def test_provider_is_privacy_disabled_by_default_without_network_call() -> None:
    provider = GeminiAIReviewProvider("key")
    with pytest.raises(AIReviewProviderError) as caught:
        provider.generate(_snapshot())
    assert caught.value.failure is AIReviewProviderFailure.PRIVACY_DISABLED
