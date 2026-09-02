import html
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import aiohttp
from aiohttp import web


DISCORD_API = "https://discord.com/api/v10"
WEB_ROOT = Path(__file__).with_name("web")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
BROWSER_COOKIE = "__Secure-sickwallet-session"


def api_error(code: str, message: str, *, status: int) -> web.Response:
    """Return the stable v1 companion error envelope."""
    return web.json_response(
        {"error": {"code": code, "message": message}},
        status=status,
        headers=SECURITY_HEADERS,
    )


def page(title: str, message: str, *, status: int = 200) -> web.Response:
    """Return a minimal non-cacheable companion page."""

    body = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem;}"
        "main{border:1px solid #ccc;border-radius:12px;padding:1.5rem}</style></head>"
        f"<body><main><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p></main></body></html>"
    )
    return web.Response(
        text=body,
        content_type="text/html",
        status=status,
        headers=SECURITY_HEADERS,
    )


class CompanionServer:
    """Loopback HTTP service intended to sit behind an HTTPS reverse proxy."""

    def __init__(self, cog):
        self.cog = cog
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None

    async def start(self, host: str, port: int) -> None:
        if self.running:
            return
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_get("/", self.home)
        app.router.add_get("/recovery", self.recovery)
        app.router.add_get("/security", self.security)
        app.router.add_get("/session", self.session_page)
        app.router.add_get("/assets/app.js", self.app_script)
        app.router.add_get("/assets/styles.css", self.styles)
        app.router.add_get("/health", self.health)
        app.router.add_get("/session/{token}", self.begin_session)
        app.router.add_get("/oauth/callback", self.oauth_callback)
        app.router.add_get("/api/v1/session", self.api_session)
        app.router.add_post("/api/v1/pair", self.api_pair)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        try:
            self.site = web.TCPSite(self.runner, host=host, port=port)
            await self.site.start()
        except Exception:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            raise

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
        self.runner = None
        self.site = None

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"status": "ok", "service": "cryptowallet-companion"},
            headers=SECURITY_HEADERS,
        )

    @staticmethod
    def _static_response(filename: str, content_type: str) -> web.Response:
        try:
            content = (WEB_ROOT / filename).read_text(encoding="utf-8")
        except OSError:
            return page("Unavailable", "The wallet interface asset is unavailable.", status=503)
        return web.Response(
            text=content,
            content_type=content_type,
            headers=SECURITY_HEADERS,
        )

    async def home(self, request: web.Request) -> web.Response:
        return self._static_response("index.html", "text/html")

    async def recovery(self, request: web.Request) -> web.Response:
        return self._static_response("recovery.html", "text/html")

    async def security(self, request: web.Request) -> web.Response:
        return self._static_response("security.html", "text/html")

    async def session_page(self, request: web.Request) -> web.Response:
        return self._static_response("session.html", "text/html")

    async def app_script(self, request: web.Request) -> web.Response:
        return self._static_response("app.js", "application/javascript")

    async def styles(self, request: web.Request) -> web.Response:
        return self._static_response("styles.css", "text/css")

    async def begin_session(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        session = await self.cog.resolve_approval_session(token)
        if session is None:
            return page(
                "Link unavailable",
                "This link is invalid, expired, or already used.",
                status=404,
            )

        oauth = await self.cog.discord_oauth_config()
        if oauth is None:
            return page("Configuration unavailable", "Discord OAuth is not configured.", status=503)
        params = urlencode(
            {
                "client_id": oauth["client_id"],
                "redirect_uri": oauth["redirect_uri"],
                "response_type": "code",
                "scope": "identify",
                "state": token,
            }
        )
        raise web.HTTPFound(
            f"https://discord.com/oauth2/authorize?{params}", headers=SECURITY_HEADERS
        )

    async def api_session(self, request: web.Request) -> web.Response:
        """Return public, server-authoritative details for a verified browser session."""
        browser_token = request.cookies.get(BROWSER_COOKIE, "")
        session = await self.cog.resolve_browser_session(browser_token)
        if session is None:
            return api_error(
                "session_unavailable",
                "The wallet session is missing, invalid, or expired.",
                status=401,
            )
        payload = await self.cog.companion_session_payload(session)
        return web.json_response({"data": payload}, headers=SECURITY_HEADERS)

    async def api_pair(self, request: web.Request) -> web.Response:
        """Exchange a short-lived owner code for website-server credentials once."""
        if request.content_type != "application/json":
            return api_error("invalid_request", "A JSON request is required.", status=415)
        try:
            body = await request.json()
            code = str(body["code"])
        except (KeyError, TypeError, ValueError):
            return api_error("invalid_request", "A pairing code is required.", status=400)
        credentials = await self.cog.complete_companion_pairing(code)
        if credentials is None:
            return api_error("pairing_rejected", "The pairing code is invalid or expired.", status=403)
        return web.json_response({"data": credentials}, status=201, headers=SECURITY_HEADERS)

    async def oauth_callback(self, request: web.Request) -> web.Response:
        code = request.query.get("code")
        token = request.query.get("state")
        if not code or not token:
            return page(
                "Verification failed",
                "Discord did not return the required state.",
                status=400,
            )

        session = await self.cog.resolve_approval_session(token)
        oauth = await self.cog.discord_oauth_config()
        if session is None or oauth is None:
            return page(
                "Link unavailable",
                "This link is invalid, expired, or already used.",
                status=404,
            )

        form = {
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": oauth["redirect_uri"],
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(f"{DISCORD_API}/oauth2/token", data=form) as response:
                    if response.status != 200:
                        return page(
                            "Verification failed",
                            "Discord rejected the authorization.",
                            status=400,
                        )
                    access_token = (await response.json())["access_token"]
                headers = {"Authorization": f"Bearer {access_token}"}
                async with client.get(f"{DISCORD_API}/users/@me", headers=headers) as response:
                    if response.status != 200:
                        return page(
                            "Verification failed",
                            "Discord identity lookup failed.",
                            status=400,
                        )
                    discord_user_id = int((await response.json())["id"])
        except (aiohttp.ClientError, KeyError, TypeError, ValueError):
            return page(
                "Verification failed",
                "Discord verification could not be completed.",
                status=502,
            )

        if discord_user_id != session.discord_user_id:
            return page(
                "Wrong Discord account",
                "Sign in with the same Discord account that requested this link.",
                status=403,
            )
        browser_token = await self.cog.establish_browser_session(token, discord_user_id)
        if browser_token is None:
            return page("Link unavailable", "This link was already used or expired.", status=409)
        approval_base_url = await self.cog.config.approval_base_url()
        cookie_path = urlparse(approval_base_url).path or "/"
        response = web.HTTPFound(
            f"{approval_base_url}/session",
            headers=SECURITY_HEADERS,
        )
        response.set_cookie(
            BROWSER_COOKIE,
            browser_token,
            max_age=max(0, session.expires_at - int(time.time())),
            httponly=True,
            secure=True,
            samesite="Strict",
            path=cookie_path,
        )
        raise response
