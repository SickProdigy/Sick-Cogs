import os
import unittest
from unittest.mock import patch

from imagine.models import ImageRequest
from imagine.providers.codex import CodexImageProvider
from imagine.providers.base import ProviderNotConfigured


class CodexProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_environment_does_not_forward_unrelated_secrets(self):
        with patch.dict(os.environ, {"PATH": "/bin", "BOT_SECRET": "nope"}, clear=True):
            self.assertEqual(CodexImageProvider._environment(), {"PATH": "/bin"})

    def test_image_signatures_are_validated(self):
        self.assertEqual(CodexImageProvider._media_type(b"\x89PNG\r\n\x1a\nrest"), "image/png")
        self.assertEqual(CodexImageProvider._media_type(b"\xff\xd8\xffrest"), "image/jpeg")
        self.assertIsNone(CodexImageProvider._media_type(b"not an image"))

    def test_timeout_is_bounded(self):
        self.assertEqual(CodexImageProvider(timeout_seconds=1).timeout_seconds, 30)
        self.assertEqual(CodexImageProvider(timeout_seconds=9999).timeout_seconds, 900)

    def test_prompt_includes_requested_output_settings(self):
        request = ImageRequest(
            "small robot", 1, 2, size="1024x1024", quality="low", background="auto"
        )
        prompt = CodexImageProvider._build_prompt(request)
        self.assertIn("Size: 1024x1024", prompt)
        self.assertIn("Quality: low", prompt)
        self.assertIn("Background: auto", prompt)
        self.assertTrue(prompt.endswith("USER DESCRIPTION:\nsmall robot"))

    def test_usage_is_read_from_completed_turn(self):
        output = (b'{"type":"turn.started"}\n'
                  b'{"type":"turn.completed","usage":{"input_tokens":12,'
                  b'"cached_input_tokens":3,"output_tokens":4,"reasoning_output_tokens":1}}\n')
        self.assertEqual(CodexImageProvider._usage_from_jsonl(output), {
            "input_tokens": 12,
            "cached_input_tokens": 3,
            "output_tokens": 4,
            "reasoning_output_tokens": 1,
        })

    async def test_missing_cli_has_actionable_error(self):
        provider = CodexImageProvider(executable="definitely-not-a-real-codex-binary")
        request = ImageRequest("robot", 1, 2)
        with self.assertRaisesRegex(ProviderNotConfigured, "not installed"):
            await provider.generate(request)
