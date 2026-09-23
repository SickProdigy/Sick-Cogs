import unittest

from mybb.mybb import MyBB


class ForumAliasTests(unittest.TestCase):
    def test_normalizes_alias(self):
        self.assertEqual(MyBB.normalize_forum_alias(" Ideas-Box "), "ideas-box")

    def test_allows_underscore_and_unicode_letters(self):
        self.assertEqual(MyBB.normalize_forum_alias("idéas_box"), "idéas_box")

    def test_rejects_reserved_destinations(self):
        for value in ("default", "120"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MyBB.normalize_forum_alias(value)

    def test_rejects_invalid_alias(self):
        for value in ("", "two words", "ideas!", "a" * 33):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MyBB.normalize_forum_alias(value)


if __name__ == "__main__":
    unittest.main()
