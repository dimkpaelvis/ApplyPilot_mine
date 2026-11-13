"""Regression tests for malformed and truncated OpenAI-compatible LLM responses."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from applypilot.llm import LLMClient, ModelEntry, _extract_openai_compat_text


def test_extracts_normal_assistant_text():
    payload = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "  generated text  "},
        }]
    }
    assert _extract_openai_compat_text(payload) == "generated text"


def test_extracts_text_content_blocks():
    payload = {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "output_text", "text": "second"},
                ]
            },
        }]
    }
    assert _extract_openai_compat_text(payload) == "first\nsecond"


@pytest.mark.parametrize(
    "payload, expected",
    [
        (None, "not a JSON object"),
        ({}, "no choices"),
        ({"choices": [{"finish_reason": "stop"}]}, "message is missing"),
        (
            {"choices": [{"finish_reason": "stop", "message": {"role": "assistant"}}]},
            "missing or empty",
        ),
        (
            {
                "choices": [{
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": "partial output"},
                }]
            },
            "truncated",
        ),
        (
            {
                "choices": [{
                    "finish_reason": "content_filter",
                    "message": {"role": "assistant", "content": "blocked"},
                }]
            },
            "content filter",
        ),
    ],
)
def test_rejects_unusable_responses(payload, expected):
    with pytest.raises(ValueError, match=expected):
        _extract_openai_compat_text(payload)


def _client():
    client = LLMClient.__new__(LLMClient)
    client.model = "gemini-3.6-flash"
    client._exhausted = {}
    client._client = Mock()
    return client


def _response(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_incomplete_primary_response_returns_none_for_fallback():
    client = _client()
    entry = ModelEntry(
        "gemini-3.6-flash", "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai", "test-key",
    )
    client._client.post.return_value = _response({
        "choices": [{"finish_reason": "length", "message": {"role": "assistant"}}]
    })

    result = client._try_openai_compat(
        entry, [{"role": "user", "content": "test"}], 0, 8192, is_last=False
    )
    assert result is None


def test_incomplete_final_response_raises_descriptive_error_not_keyerror():
    client = _client()
    entry = ModelEntry(
        "gemini-3.6-flash", "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai", "test-key",
    )
    client._client.post.return_value = _response({
        "choices": [{"finish_reason": "length", "message": {"role": "assistant"}}]
    })

    with pytest.raises(RuntimeError, match="truncated"):
        client._try_openai_compat(
            entry, [{"role": "user", "content": "test"}], 0, 8192, is_last=True
        )
