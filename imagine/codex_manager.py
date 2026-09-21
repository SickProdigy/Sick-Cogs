import asyncio
import json
import os
import shutil
from contextlib import suppress
from pathlib import Path

import aiohttp


INSTALLER_URL = "https://chatgpt.com/codex/install.sh"
MAX_INSTALLER_BYTES = 512 * 1024


class CodexManagerError(RuntimeError):
    """A safe, owner-facing Codex lifecycle error."""


class CodexAppServer:
    def __init__(self, executable: Path, environment: dict):
        self.executable = executable
        self.environment = environment
        self.process = None
        self._reader_task = None
        self._stderr_task = None
        self._pending = {}
        self._notifications = asyncio.Queue()
        self._next_id = 1

    async def start(self):
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(self.executable), "app-server", "--listen", "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.environment,
            )
        except FileNotFoundError as exc:
            raise CodexManagerError("Codex CLI is not installed.") from exc
        except OSError as exc:
            raise CodexManagerError("Codex CLI could not be started on this host.") from exc
        self._reader_task = asyncio.create_task(self._read_messages())
        self._stderr_task = asyncio.create_task(self.process.stderr.read())
        await self.request("initialize", {
            "clientInfo": {
                "name": "sick-cogs-imagine",
                "title": "Sick-Cogs Imagine",
                "version": "0.2.0",
            }
        })
        await self.notify("initialized", {})
        return self

    async def _read_messages(self):
        while self.process and (line := await self.process.stdout.readline()):
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            request_id = message.get("id")
            if request_id is not None and request_id in self._pending:
                future = self._pending.pop(request_id)
                if "error" in message:
                    future.set_exception(CodexManagerError(
                        message["error"].get("message", "Codex returned an error.")
                    ))
                else:
                    future.set_result(message.get("result", {}))
            elif message.get("method"):
                await self._notifications.put(message)
        error = CodexManagerError("Codex closed before completing the request.")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _send(self, message):
        if not self.process or not self.process.stdin:
            raise CodexManagerError("Codex is not running.")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params=None, timeout=30):
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"id": request_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise CodexManagerError("Codex did not respond in time.") from exc

    async def notify(self, method, params=None):
        await self._send({"method": method, "params": params or {}})

    async def wait_for_login(self, login_id, timeout=600):
        async def wait():
            while True:
                message = await self._notifications.get()
                if message.get("method") != "account/login/completed":
                    continue
                params = message.get("params", {})
                if params.get("loginId") == login_id:
                    return params

        try:
            return await asyncio.wait_for(wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            with suppress(CodexManagerError):
                await self.request("account/login/cancel", {"loginId": login_id})
            raise CodexManagerError("Codex linking expired. Start it again when ready.") from exc

    async def close(self):
        if self.process and self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), timeout=5)
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        self.process = None


class CodexManager:
    def __init__(self, data_path: Path, session_getter):
        self.data_path = Path(data_path)
        self.bin_path = self.data_path / "bin" / "codex"
        self.codex_home = self.data_path / "codex-home"
        self._session_getter = session_getter
        self._install_lock = asyncio.Lock()

    def executable(self):
        if self.bin_path.is_file() and os.access(self.bin_path, os.X_OK):
            return self.bin_path
        found = shutil.which("codex")
        return Path(found) if found else None

    def environment(self):
        allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE",
                   "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        if self.bin_path.is_file():
            environment["CODEX_HOME"] = str(self.codex_home)
        return environment

    async def version(self):
        executable = self.executable()
        if not executable:
            return None
        try:
            process = await asyncio.create_subprocess_exec(
                str(executable), "--version", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self.environment()
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            return None
        return stdout.decode(errors="replace").strip() if process.returncode == 0 else None

    async def install(self):
        async with self._install_lock:
            session = self._session_getter()
            if not session:
                raise CodexManagerError("Imagine is still starting.")
            try:
                async with session.get(INSTALLER_URL, allow_redirects=True) as response:
                    response.raise_for_status()
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_INSTALLER_BYTES:
                            raise CodexManagerError(
                                "The Codex installer response was unexpectedly large."
                            )
                    installer = bytes(chunks)
            except CodexManagerError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise CodexManagerError("Could not download the official Codex installer.") from exc
            if not installer.startswith(b"#!/bin/sh") or b"releases.openai.com/codex" not in installer:
                raise CodexManagerError("The downloaded Codex installer did not pass validation.")

            self.bin_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
            environment = self.environment()
            environment.update({
                "CODEX_HOME": str(self.codex_home),
                "CODEX_INSTALL_DIR": str(self.bin_path.parent),
                "CODEX_NON_INTERACTIVE": "true",
                "PATH": f"{self.bin_path.parent}:{environment.get('PATH', '')}",
            })
            process = await asyncio.create_subprocess_exec(
                "/bin/sh", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=environment
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(installer), timeout=420)
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.wait()
                raise CodexManagerError("Codex installation timed out.") from exc
            if process.returncode or not (self.bin_path.is_file() and os.access(self.bin_path, os.X_OK)):
                detail = stderr.decode(errors="replace").strip().splitlines()
                suffix = f" ({detail[-1][:300]})" if detail else ""
                raise CodexManagerError(f"Codex installation failed{suffix}.")
            return await self.version()

    async def open_server(self):
        executable = self.executable()
        if not executable:
            raise CodexManagerError("Install Codex first.")
        return await CodexAppServer(executable, self.environment()).start()

    async def account(self):
        server = await self.open_server()
        try:
            result = await server.request("account/read", {"refreshToken": False})
            return result.get("account")
        finally:
            await server.close()

    async def usage(self):
        server = await self.open_server()
        try:
            return await server.request("account/usage/read")
        finally:
            await server.close()

    async def rate_limits(self):
        server = await self.open_server()
        try:
            return await server.request("account/rateLimits/read")
        finally:
            await server.close()

    async def logout(self):
        server = await self.open_server()
        try:
            await server.request("account/logout")
        finally:
            await server.close()

    async def begin_device_login(self):
        server = await self.open_server()
        try:
            result = await server.request(
                "account/login/start", {"type": "chatgptDeviceCode"}, timeout=60
            )
        except Exception:
            await server.close()
            raise
        required = ("loginId", "verificationUrl", "userCode")
        if not all(result.get(key) for key in required):
            await server.close()
            raise CodexManagerError("Codex did not return a complete linking request.")
        return server, result
