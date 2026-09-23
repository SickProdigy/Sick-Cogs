import unittest

from twitter.client import new_posts, normalize_username


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


if __name__ == "__main__":
    unittest.main()
