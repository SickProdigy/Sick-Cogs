import json
import unittest
from types import SimpleNamespace

from navidrome.client import (
    NavidromeClient, NavidromeError, validate_base_url, validate_public_base_url,
)


class FakeContent:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        for offset in range(0, len(self.data), size):
            yield self.data[offset:offset + size]


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None):
        self.status = status
        self.payload = payload
        self._text = text
        self.headers = {}
        data = text.encode() if text is not None else json.dumps(payload).encode()
        self.content = FakeContent(data)
        self.connection = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self.payload

    async def text(self):
        if self._text is not None:
            return self._text
        if self.payload is None:
            return ""
        import json
        return json.dumps(self.payload)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def request(self, method, url, **kwargs):
        return self._next(method, url, kwargs)


class NavidromeClientTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_base_url_requires_https_by_default(self):
        self.assertEqual(
            validate_base_url("https://music.example.com/"), "https://music.example.com"
        )
        with self.assertRaises(ValueError):
            validate_base_url("http://music.example.com")

    def test_validate_base_url_allows_explicit_http_but_never_embedded_credentials(self):
        self.assertEqual(
            validate_base_url("http://192.168.1.5:4533", allow_http=True),
            "http://192.168.1.5:4533",
        )
        with self.assertRaises(ValueError):
            validate_base_url("https://admin:secret@music.example.com")

    def test_validate_base_url_rejects_query_fragment_and_missing_host(self):
        for value in (
            "https://music.example.com/?token=secret",
            "https://music.example.com/#fragment",
            "https:///missing-host",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_base_url(value)

    async def test_public_validation_rejects_private_ip_literal(self):
        with self.assertRaisesRegex(ValueError, "public"):
            await validate_public_base_url("https://127.0.0.1:4533")

    async def test_public_validation_allows_global_ip_literal(self):
        self.assertEqual(
            await validate_public_base_url("https://8.8.8.8"), "https://8.8.8.8"
        )

    async def test_json_response_rejects_declared_oversize_payload(self):
        response = FakeResponse(payload={"ok": True})
        response.headers["Content-Length"] = "2000001"
        client = NavidromeClient(
            SimpleNamespace(), "https://music.example.com", "bot", "secret"
        )
        with self.assertRaisesRegex(NavidromeError, "too large"):
            await client._json_response(response)

    def test_public_only_rejects_response_without_verified_public_peer(self):
        client = NavidromeClient(
            SimpleNamespace(), "https://music.example.com", "bot", "secret", public_only=True
        )
        with self.assertRaisesRegex(NavidromeError, "unsafe destination"):
            client._validate_response_peer(FakeResponse(payload={}))

    def test_subsonic_auth_uses_salted_token_not_plaintext_password(self):
        client = NavidromeClient(
            SimpleNamespace(), "https://music.example.com", "bot", "top-secret"
        )
        params = client._auth_params()
        self.assertEqual(params["u"], "bot")
        self.assertTrue(params["t"])
        self.assertTrue(params["s"])
        self.assertNotIn("p", params)
        self.assertNotIn("top-secret", params.values())

    async def test_search_returns_artist_and_album_results(self):
        session = FakeSession([FakeResponse(payload={
            "subsonic-response": {"status": "ok", "searchResult3": {
                "artist": [{"id": "a"}], "album": [{"id": "b"}]
            }}
        })])
        client = NavidromeClient(session, "https://music.example.com", "bot", "secret")
        result = await client.search("example")
        self.assertEqual(result["artists"][0]["id"], "a")
        self.assertEqual(result["albums"][0]["id"], "b")
        self.assertEqual(session.calls[0][2]["params"]["songCount"], "0")

    async def test_native_user_list_authenticates_as_admin(self):
        session = FakeSession([
            FakeResponse(payload={"token": "jwt", "isAdmin": True}),
            FakeResponse(payload=[{"id": "1", "userName": "alice"}]),
        ])
        client = NavidromeClient(session, "https://music.example.com", "admin", "secret")

        users = await client.users()

        self.assertEqual(users[0]["userName"], "alice")
        self.assertEqual(session.calls[0][1], "https://music.example.com/auth/login")
        self.assertEqual(session.calls[1][1], "https://music.example.com/api/user/")
        self.assertEqual(
            session.calls[1][2]["headers"]["X-ND-Authorization"], "Bearer jwt"
        )

    async def test_native_user_management_requires_admin_credentials(self):
        session = FakeSession([
            FakeResponse(payload={"token": "jwt", "isAdmin": False}),
        ])
        client = NavidromeClient(session, "https://music.example.com", "listener", "secret")

        with self.assertRaisesRegex(NavidromeError, "administrator"):
            await client.users()

    async def test_create_user_never_returns_or_persists_password(self):
        session = FakeSession([
            FakeResponse(payload={"token": "jwt", "isAdmin": True}),
            FakeResponse(status=201, payload={"id": "new-id"}),
            FakeResponse(payload={
                "id": "new-id", "userName": "alice", "name": "Alice",
                "email": "", "isAdmin": False,
            }),
        ])
        client = NavidromeClient(session, "https://music.example.com", "admin", "secret")

        user = await client.create_user("alice", "temporary-secret", name="Alice")

        self.assertEqual(user["id"], "new-id")
        create_payload = session.calls[1][2]["json"]
        self.assertEqual(create_payload["password"], "temporary-secret")
        self.assertNotIn("password", user)
        self.assertEqual([call[0] for call in session.calls].count("POST"), 2)
        self.assertEqual(session.calls[0][1], "https://music.example.com/auth/login")

    async def test_missing_cover_art_never_builds_an_external_authenticated_url(self):
        client = NavidromeClient(
            SimpleNamespace(), "https://music.example.com", "bot", "secret"
        )
        self.assertIsNone(await client.cover_art(None))


if __name__ == "__main__":
    unittest.main()
