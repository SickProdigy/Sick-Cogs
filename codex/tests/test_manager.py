import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex.manager import CodexManager, CodexManagerError


class FakeServer:
    def __init__(self, result):
        self.result = result
        self.closed = False

    async def request(self, method, params=None, timeout=30):
        return self.result

    async def close(self):
        self.closed = True


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_shared_binary_is_separate_from_user_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = CodexManager(
                root / "Codex", lambda: None, root / "SickCogsShared" / "codex"
            )
            self.assertEqual(
                manager.bin_path, root / "SickCogsShared" / "codex" / "bin" / "codex"
            )
            self.assertEqual(manager.accounts_path, root / "Codex" / "accounts")

    def test_users_receive_isolated_private_codex_homes(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CodexManager(Path(directory), lambda: None)
            with patch.dict(
                os.environ, {"PATH": "/bin", "BOT_SECRET": "nope"}, clear=True
            ):
                first = manager.environment(1)
                second = manager.environment(2)
            self.assertNotEqual(first["CODEX_HOME"], second["CODEX_HOME"])
            self.assertNotIn("BOT_SECRET", first)
            self.assertEqual(
                Path(first["CODEX_HOME"]).stat().st_mode & 0o777, 0o700
            )

    async def test_device_login_requires_complete_response(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = CodexManager(Path(directory), lambda: None)
            server = FakeServer(
                {"loginId": "one", "verificationUrl": "https://example.com"}
            )

            async def open_server(user_id):
                return server

            manager.open_server = open_server
            with self.assertRaisesRegex(
                CodexManagerError, "complete linking request"
            ):
                await manager.begin_device_login(1)
            self.assertTrue(server.closed)


if __name__ == "__main__":
    unittest.main()
