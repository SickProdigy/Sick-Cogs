import unittest
from types import SimpleNamespace

from navidrome.client import NavidromeClient, validate_base_url


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

    async def test_missing_cover_art_never_builds_an_external_authenticated_url(self):
        client = NavidromeClient(
            SimpleNamespace(), "https://music.example.com", "bot", "secret"
        )
        self.assertIsNone(await client.cover_art(None))


if __name__ == "__main__":
    unittest.main()
