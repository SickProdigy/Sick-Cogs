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

    async def test_missing_cli_has_actionable_error(self):
        provider = CodexImageProvider(executable="definitely-not-a-real-codex-binary")
        request = ImageRequest("robot", 1, 2)
        with self.assertRaisesRegex(ProviderNotConfigured, "not installed"):
            await provider.generate(request)
