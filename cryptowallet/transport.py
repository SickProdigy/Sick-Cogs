import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlparse, urlunparse

import aiohttp

from .pairing import PAIRING_TOKEN_NAMESPACE


log = logging.getLogger("red.Sick-Cogs.CryptoWallet.Transport")

PAIRING_INPUT_NAMESPACE = "cryptowallet_broker_pairing"
CONNECT_TIMEOUT_SECONDS = 15
MAX_MESSAGE_BYTES = 64 * 1024
MAX_BACKOFF_SECONDS = 60


class BrokerTransportError(RuntimeError):
    """Raised when the website broker cannot be paired or authenticated."""


def _broker_urls(approval_base_url: str) -> tuple[str, str]:
    parsed = urlparse(approval_base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BrokerTransportError("The companion website URL must use HTTPS.")
    base_path = parsed.path.rstrip("/")
    pair_path = f"{base_path}/broker/v1/pair"
    socket_path = f"{base_path}/socket"
    pair_url = urlunparse(("https", parsed.netloc, pair_path, "", "", ""))
    socket_url = urlunparse(("wss", parsed.netloc, socket_path, "", "", ""))
    return pair_url, socket_url


def _connection_signature(
    credential: str,
    timestamp: str,
    nonce: str,
    installation_id: str,
) -> str:
    canonical = "\n".join(
        (
            "sickwallet-ws-v1",
            timestamp,
            nonce,
            installation_id,
            "GET",
            "/v1/socket",
        )
    )
    return hmac.new(
        credential.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class BrokerTransport:
    """Maintain the cog's authenticated outbound connection to the website broker."""

    def __init__(self, cog):
        self.cog = cog
        self.task: asyncio.Task | None = None
        self.socket: aiohttp.ClientWebSocketResponse | None = None
        self.connected = asyncio.Event()
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def pair(self, code: str) -> dict:
        approval_base_url = await self.cog.config.approval_base_url()
        deployment_id = await self.cog.config.deployment_id()
        application_id = self.cog.discord_application_id()
        if not approval_base_url or not deployment_id or application_id is None:
            raise BrokerTransportError(
                "Configure the companion URL and initialize the cog before pairing."
            )
        pair_url, _ = _broker_urls(approval_base_url)
        timeout = aiohttp.ClientTimeout(total=CONNECT_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    pair_url,
                    json={
                        "code": code,
                        "deployment_id": deployment_id,
                        "discord_application_id": str(application_id),
                    },
                ) as response:
                    if response.status != 201:
                        raise BrokerTransportError(
                            f"The website broker rejected pairing with HTTP {response.status}."
                        )
                    payload = await response.json()
        except BrokerTransportError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise BrokerTransportError("The website broker could not be reached.") from exc
        try:
            result = payload["data"]
            installation_id = str(result["installation_id"])
            credential = str(result["credential"])
            returned_deployment = str(result["deployment_id"])
            returned_application = str(result["discord_application_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BrokerTransportError("The website broker returned invalid pairing data.") from exc
        if (
            not installation_id
            or not credential
            or returned_deployment != deployment_id
            or returned_application != str(application_id)
        ):
            raise BrokerTransportError("The website broker returned mismatched pairing data.")
        await self.stop()
        await self.cog.bot.set_shared_api_tokens(
            PAIRING_TOKEN_NAMESPACE,
            installation_id=installation_id,
            credential=credential,
        )
        await self.cog.config.paired_at.set(int(time.time()))
        await self.start()
        return {"installation_id": installation_id}

    async def start(self) -> None:
        if self.running:
            return
        tokens = await self.cog.bot.get_shared_api_tokens(PAIRING_TOKEN_NAMESPACE)
        if not tokens.get("installation_id") or not tokens.get("credential"):
            return
        if not await self.cog.config.approval_base_url():
            return
        self.task = asyncio.create_task(
            self._run(),
            name="cryptowallet-website-broker",
        )

    async def stop(self) -> None:
        task = self.task
        self.task = None
        self.connected.clear()
        if self.socket is not None and not self.socket.closed:
            await self.socket.close()
        self.socket = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        backoff = 1
        while self.task is asyncio.current_task():
            try:
                await self._connect_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except BrokerTransportError as exc:
                self.last_error = str(exc)
                log.warning("CryptoWallet website broker connection failed: %s", exc)
            except Exception:
                self.last_error = "Unexpected website broker connection failure."
                log.exception("Unexpected CryptoWallet website broker connection failure")
            finally:
                self.connected.clear()
                self.socket = None
            if self.task is not asyncio.current_task():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def _connect_once(self) -> None:
        approval_base_url = await self.cog.config.approval_base_url()
        tokens = await self.cog.bot.get_shared_api_tokens(PAIRING_TOKEN_NAMESPACE)
        installation_id = str(tokens.get("installation_id") or "")
        credential = str(tokens.get("credential") or "")
        if not approval_base_url or not installation_id or not credential:
            raise BrokerTransportError("The website broker is not completely configured.")
        _, socket_url = _broker_urls(approval_base_url)
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        headers = {
            "X-SickWallet-Installation": installation_id,
            "X-SickWallet-Timestamp": timestamp,
            "X-SickWallet-Nonce": nonce,
            "X-SickWallet-Signature": _connection_signature(
                credential,
                timestamp,
                nonce,
                installation_id,
            ),
        }
        timeout = aiohttp.ClientTimeout(
            total=None,
            sock_connect=CONNECT_TIMEOUT_SECONDS,
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(
                    socket_url,
                    headers=headers,
                    heartbeat=30,
                    max_msg_size=MAX_MESSAGE_BYTES,
                ) as socket:
                    self.socket = socket
                    message = await asyncio.wait_for(
                        socket.receive_json(),
                        timeout=CONNECT_TIMEOUT_SECONDS,
                    )
                    if (
                        message.get("type") != "welcome"
                        or int(message.get("version", 0)) != 1
                        or str(message.get("installation_id") or "") != installation_id
                    ):
                        raise BrokerTransportError(
                            "The website broker returned an invalid welcome message."
                        )
                    self.last_error = None
                    self.connected.set()
                    async for event in socket:
                        if event.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(event.data)
                        elif event.type in {
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            break
        except BrokerTransportError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise BrokerTransportError("The website broker connection was interrupted.") from exc

    async def _handle_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            if self.socket is not None:
                await self.socket.close(code=4002, message=b"Invalid JSON")
            return
        if message.get("type") == "pong":
            return
        log.debug(
            "Ignoring unsupported website broker message type %r",
            message.get("type"),
        )
