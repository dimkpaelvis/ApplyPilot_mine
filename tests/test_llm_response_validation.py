"""Regression tests for malformed and incomplete LLM provider responses."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from applypilot.llm import (
    LLMClient,
    ModelEntry,
    _extract_openai_compat_text,
)


class TestOpenAICompatibleResponseExtraction(unittest.TestCase):
    def test_extracts_normal_text(self):
        self.assertEqual(
            _extract_openai_compat_text({
                "choices": [{
                    "message": {"role": "assistant", "content": "  Hello  "},
                    "finish_reason": "stop",
                }]
            }),
            "Hello",
        )

    def test_extracts_text_content_blocks(self):
        self.assertEqual(
            _extract_openai_compat_text({
                "choices": [{
                    "message": {
                        "content": [
                            {"type": "text", "text": "First"},
                            {"type": "image", "image_url": "ignored"},
                            {"type": "output_text", "text": "Second"},
                        ]
                    },
                    "finish_reason": "stop",
                }]
            }),
            "First\nSecond",
        )

    def test_rejects_non_object_body(self):
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            _extract_openai_compat_text([])

    def test_rejects_missing_or_empty_choices(self):
        for payload in ({}, {"choices": []}, {"choices": None}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "no choices"):
                    _extract_openai_compat_text(payload)

    def test_rejects_invalid_choice(self):
        with self.assertRaisesRegex(ValueError, "first choice"):
            _extract_openai_compat_text({"choices": ["invalid"]})

    def test_rejects_missing_message(self):
        with self.assertRaisesRegex(ValueError, "message is missing"):
            _extract_openai_compat_text({
                "choices": [{"finish_reason": "stop"}]
            })

    def test_rejects_missing_content_without_keyerror(self):
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            _extract_openai_compat_text({
                "choices": [{
                    "message": {"role": "assistant"},
                    "finish_reason": "stop",
                }]
            })

    def test_rejects_empty_content(self):
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            _extract_openai_compat_text({
                "choices": [{
                    "message": {"content": "   "},
                    "finish_reason": "stop",
                }]
            })

    def test_rejects_truncated_generation(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            _extract_openai_compat_text({
                "choices": [{
                    "message": {"content": "partial output"},
                    "finish_reason": "length",
                }]
            })

    def test_rejects_content_filter_finish_reason(self):
        with self.assertRaisesRegex(ValueError, "content filter"):
            _extract_openai_compat_text({
                "choices": [{
                    "message": {"content": ""},
                    "finish_reason": "content_filter",
                }]
            })

    def test_includes_refusal_context(self):
        with self.assertRaisesRegex(ValueError, "refusal"):
            _extract_openai_compat_text({
                "choices": [{
                    "message": {
                        "content": None,
                        "refusal": "Request refused",
                    },
                    "finish_reason": "stop",
                }]
            })


class TestProviderFallbackOnInvalidResponse(unittest.TestCase):
    def setUp(self):
        self.client = LLMClient.__new__(LLMClient)
        self.client.model = "gemini-3.6-flash"
        self.client._exhausted = {}
        self.client._client = Mock()
        self.entry = ModelEntry(
            "gemini-3.6-flash",
            "gemini",
            "https://example.invalid/v1",
            "test-key",
        )

    def set_response(self, payload):
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        self.client._client.post.return_value = response

    def test_invalid_response_returns_none_when_fallback_available(self):
        self.set_response({
            "choices": [{
                "message": {"role": "assistant"},
                "finish_reason": "stop",
            }]
        })
        result = self.client._try_openai_compat(
            self.entry,
            [{"role": "user", "content": "test"}],
            0,
            512,
            False,
        )
        self.assertIsNone(result)

    def test_invalid_response_raises_clear_error_on_last_model(self):
        self.set_response({"choices": []})
        with self.assertRaisesRegex(RuntimeError, "no choices"):
            self.client._try_openai_compat(
                self.entry,
                [{"role": "user", "content": "test"}],
                0,
                512,
                True,
            )


if __name__ == "__main__":
    unittest.main()
