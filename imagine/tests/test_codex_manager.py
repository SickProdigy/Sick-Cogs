import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imagine.codex_manager import CodexManager, CodexManagerError


class FakeServer:
    def __init__(self, result):
        self.result = result
        self.closed = False

    async def request(self, method, params=None, timeout=30):
        return self.result

    async def close(self):
        self.closed = True


class CodexManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_managed_binary_uses_private_codex_home(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CodexManager(Path(directory), lambda: None)
            manager.bin_path.parent.mkdir(parents=True)
            manager.bin_path.write_bytes(b"#!/bin/sh\n")
            manager.bin_path.chmod(0o700)
            with patch.dict(os.environ, {"PATH": "/bin", "BOT_SECRET": "nope"}, clear=True):
                self.assertEqual(manager.executable(), manager.bin_path)
                self.assertEqual(manager.environment(), {
                    "PATH": "/bin", "CODEX_HOME": str(manager.codex_home)
                })

    async def test_device_login_requires_complete_response(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CodexManager(Path(directory), lambda: None)
            server = FakeServer({"loginId": "one", "verificationUrl": "https://example.com"})

            async def open_server():
                return server

            manager.open_server = open_server
            with self.assertRaisesRegex(CodexManagerError, "complete linking request"):
                await manager.begin_device_login()
            self.assertTrue(server.closed)
