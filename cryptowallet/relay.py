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


log = logging.getLogger("red.Sick-Cogs.CryptoWallet.Relay")

PAIRING_INPUT_NAMESPACE = "cryptowallet_relay_pairing"
RELAY_TRANSPORT = "relay-v1"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 64 * 1024
MAX_BACKOFF_SECONDS = 60


class RelayError(RuntimeError):
    """Raised when the website relay cannot be paired or contacted safely."""


def _relay_url(approval_base_url: str, endpoint: str) -> str:
    parsed = urlparse(approval_base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RelayError("The companion website URL must use HTTPS.")
    path = f"{parsed.path.rstrip('/')}/relay/{endpoint}.php"
    return urlunparse(("https", parsed.netloc, path, "", "", ""))


def _json_body(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signed_headers(
    credential: str,
    installation_id: str,
    method: str,
    url: str,
    body: bytes,
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    path = urlparse(url).path
    canonical = "\n".join(
        (
            RELAY_TRANSPORT,
            timestamp,
            nonce,
            method.upper(),
            path,
            hashlib.sha256(body).hexdigest(),
        )
    )
    signature = hmac.new(
        credential.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-SickWallet-Installation": installation_id,
        "X-SickWallet-Timestamp": timestamp,
        "X-SickWallet-Nonce": nonce,
        "X-SickWallet-Signature": signature,
    }


class RelayClient:
    """Poll the companion website outbound without requiring an inbound bot port."""

    def __init__(self, cog):
        self.cog = cog
        self.task: asyncio.Task | None = None
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
            raise RelayError("Configure the companion URL and initialize the cog before pairing.")
        url = _relay_url(approval_base_url, "pair")
        body = _json_body(
            {
                "code": code,
                "deployment_id": deployment_id,
                "discord_application_id": str(application_id),
            }
        )
        payload = await self._request(url, body, headers={"Content-Type": "application/json"})
        try:
            result = payload["data"]
            installation_id = str(result["installation_id"])
            credential = str(result["credential"])
            returned_deployment = str(result["deployment_id"])
            returned_application = str(result["discord_application_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RelayError("The website relay returned invalid pairing data.") from exc
        if (
            not installation_id
            or not credential
            or returned_deployment != deployment_id
            or returned_application != str(application_id)
        ):
            raise RelayError("The website relay returned mismatched pairing data.")
        await self.stop()
        await self.cog.bot.set_shared_api_tokens(
            PAIRING_TOKEN_NAMESPACE,
            installation_id=installation_id,
            credential=credential,
            transport=RELAY_TRANSPORT,
        )
        await self.cog.config.paired_at.set(int(time.time()))
        await self.start()
        return {"installation_id": installation_id}

    async def start(self) -> None:
        if self.running:
            return
        tokens = await self.cog.bot.get_shared_api_tokens(PAIRING_TOKEN_NAMESPACE)
        if tokens.get("transport") != RELAY_TRANSPORT:
            return
        if not tokens.get("installation_id") or not tokens.get("credential"):
            return
        if not await self.cog.config.approval_base_url():
            return
        self.task = asyncio.create_task(self._run(), name="cryptowallet-website-relay")

    async def stop(self) -> None:
        task = self.task
        self.task = None
        self.connected.clear()
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
                message = await self._poll()
                self.connected.set()
                self.last_error = None
                backoff = 1
                if message is not None:
                    await self._complete(message, await self._dispatch(message))
            except asyncio.CancelledError:
                raise
            except RelayError as exc:
                self.connected.clear()
                self.last_error = str(exc)
                log.warning("CryptoWallet website relay failed: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            except Exception:
                self.connected.clear()
                self.last_error = "Unexpected website relay failure."
                log.exception("Unexpected CryptoWallet website relay failure")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def _credentials(self) -> tuple[str, str, str]:
        approval_base_url = await self.cog.config.approval_base_url()
        tokens = await self.cog.bot.get_shared_api_tokens(PAIRING_TOKEN_NAMESPACE)
        installation_id = str(tokens.get("installation_id") or "")
        credential = str(tokens.get("credential") or "")
        if (
            not approval_base_url
            or tokens.get("transport") != RELAY_TRANSPORT
            or not installation_id
            or not credential
        ):
            raise RelayError("The website relay is not completely configured.")
        return approval_base_url, installation_id, credential

    async def _poll(self) -> dict | None:
        approval_base_url, installation_id, credential = await self._credentials()
        url = _relay_url(approval_base_url, "poll")
        body = _json_body({})
        payload = await self._request(
            url,
            body,
            headers=_signed_headers(credential, installation_id, "POST", url, body),
        )
        message = payload.get("data", {}).get("message")
        if message is not None and not isinstance(message, dict):
            raise RelayError("The website relay returned an invalid message.")
        return message

    async def _complete(self, message: dict, result: dict) -> None:
        approval_base_url, installation_id, credential = await self._credentials()
        url = _relay_url(approval_base_url, "complete")
        try:
            request_id = str(message["request_id"])
            lease_token = str(message["lease_token"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RelayError("The website relay message omitted its lease.") from exc
        body = _json_body(
            {
                "request_id": request_id,
                "lease_token": lease_token,
                "result": result,
            }
        )
        await self._request(
            url,
            body,
            headers=_signed_headers(credential, installation_id, "POST", url, body),
        )

    async def _dispatch(self, message: dict) -> dict:
        if message.get("operation") == "probe":
            return {"ok": True, "handled_at": int(time.time())}
        return {"ok": False, "error": "unsupported_operation"}

    @staticmethod
    async def _request(url: str, body: bytes, *, headers: dict[str, str]) -> dict:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=body, headers=headers) as response:
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(16 * 1024):
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            break
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise RelayError("The website relay returned an oversized response.")
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RelayError("The website relay returned invalid JSON.") from exc
                    if not isinstance(payload, dict):
                        raise RelayError("The website relay returned an invalid response.")
                    if response.status < 200 or response.status >= 300:
                        code = payload.get("error", {}).get("code", "request_failed")
                        raise RelayError(
                            f"The website relay rejected the request ({code}, HTTP {response.status})."
                        )
                    return payload
        except RelayError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RelayError("The website relay could not be reached.") from exc
