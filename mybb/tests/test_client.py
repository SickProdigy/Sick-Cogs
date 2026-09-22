import unittest

from mybb.client import MyBBClient, normalize_base_url


class MyBBClientTests(unittest.TestCase):
    def test_normalizes_site_root(self):
        client = MyBBClient("https://forum.example.com/")
        self.assertEqual(client.base_url, "https://forum.example.com")
        self.assertEqual(client.api_url, "https://forum.example.com/api/v1")

    def test_preserves_subdirectory(self):
        client = MyBBClient("https://example.com/community/api/v1")
        self.assertEqual(client.base_url, "https://example.com/community")
        self.assertEqual(client.api_url, "https://example.com/community/api/v1")

    def test_rejects_plain_http(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            MyBBClient("http://forum.example.com")

    def test_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "without credentials"):
            normalize_base_url("https://user:pass@example.com")

    def test_rejects_query_and_fragment(self):
        for value in ("https://example.com/?x=1", "https://example.com/#fragment"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_base_url(value)

    def test_rejects_private_ip_literals(self):
        for value in ("https://127.0.0.1", "https://10.0.0.5", "https://[::1]"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "private"):
                normalize_base_url(value)

    def test_strips_token_whitespace(self):
        self.assertEqual(MyBBClient("https://forum.example.com", " secret ").token, "secret")


if __name__ == "__main__":
    unittest.main()
