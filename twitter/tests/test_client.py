import unittest

from twitter.client import MAX_TIMELINE_PAGES, XAPIError, XClient, new_posts, normalize_username


class TwitterClientTests(unittest.TestCase):
    def test_normalizes_username(self):
        self.assertEqual(normalize_username(" @XDevelopers "), "xdevelopers")

    def test_rejects_invalid_username(self):
        for value in ("", "two words", "bad-name", "idéas", "x" * 16):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_username(value)

    def test_filters_and_orders_new_posts(self):
        posts = [{"id": "12"}, {"id": "10"}, {"id": "bad"}, {"id": "11"}]
        self.assertEqual([post["id"] for post in new_posts(posts, "10")], ["11", "12"])

    def test_empty_cursor_starts_at_zero(self):
        self.assertEqual([post["id"] for post in new_posts([{"id": "1"}], None)], ["1"])


class StubClient(XClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.bearer_token = "test"
        self.session = None

    async def request(self, path, *, params=None):
        self.calls.append((path, dict(params or {})))
        return self.responses.pop(0)


class TwitterPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeline_pages_are_combined(self):
        client = StubClient([
            {"data": [{"id": "12"}], "meta": {"next_token": "next"}},
            {"data": [{"id": "11"}], "meta": {}},
        ])
        posts = await client.user_posts("123", "10")
        self.assertEqual({post["id"] for post in posts}, {"11", "12"})
        self.assertNotIn("pagination_token", client.calls[0][1])
        self.assertEqual(client.calls[1][1]["pagination_token"], "next")

    async def test_invalid_saved_identifiers_are_rejected(self):
        client = StubClient([])
        with self.assertRaises(XAPIError):
            await client.user_posts("not-an-id", "10")
        with self.assertRaises(XAPIError):
            await client.user_posts("123", "not-a-cursor")

    async def test_bounded_pagination_does_not_advance_a_partial_timeline(self):
        client = StubClient([
            {"data": [{"id": str(index + 1)}], "meta": {"next_token": str(index + 1)}}
            for index in range(MAX_TIMELINE_PAGES)
        ])
        with self.assertRaises(XAPIError):
            await client.user_posts("123", "0")


if __name__ == "__main__":
    unittest.main()
