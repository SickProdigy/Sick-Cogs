import unittest

from ark.client import classify_news, extract_image, new_items, plain_text, recent_items


class ArkClientTests(unittest.TestCase):
    def test_classifies_community_before_embedded_update_words(self):
        item = {"title": "Community Crunch 500: Update Preview", "contents": "A patch is coming."}
        self.assertEqual(classify_news(item), "community")

    def test_classifies_primary_categories(self):
        cases = [
            ("Server Hotfix", "hotfixes"),
            ("ARKpocalypse servers have been wiped", "wipes"),
            ("Summer Event Begins", "events"),
            ("UE 5.8 Update is live", "updates"),
            ("New map out now", "releases"),
            ("ARK Franchise Sale", "promotions"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_news({"title": title, "contents": ""}), expected)

    def test_unknown_is_official(self):
        self.assertEqual(classify_news({"title": "Creature spotlight", "contents": "Details"}), "official")

    def test_plain_text_removes_steam_markup(self):
        source = "[h1]Patch Notes[/h1]\n[list][*][color=#fff][b]Fixed[/b][/color] bugs &amp; crashes.[/list]\n[img]https://example.com/a.png[/img]"
        self.assertEqual(plain_text(source), "Patch Notes\n• Fixed bugs & crashes.")

    def test_extracts_and_expands_steam_clan_image(self):
        source = "[img]{STEAM_CLAN_IMAGE}/12345/banner.jpg[/img]"
        self.assertEqual(
            extract_image(source),
            "https://clan.cloudflare.steamstatic.com/images/12345/banner.jpg",
        )

    def test_prefers_artwork_before_full_resolution_link(self):
        source = (
            "[img]{STEAM_CLAN_IMAGE}/12345/header.png[/img]\n"
            "Introduction\n"
            "[img]{STEAM_CLAN_IMAGE}/12345/featured.jpg[/img]\n"
            "[url=https://drive.google.com/example][color=#3498db]"
            "Download in full resolution[/color][/url]"
        )
        self.assertEqual(
            extract_image(source),
            "https://clan.cloudflare.steamstatic.com/images/12345/featured.jpg",
        )

    def test_recent_items_selects_newest_but_returns_oldest_first(self):
        items = [
            {"gid": "1", "date": 10},
            {"gid": "3", "date": 30},
            {"gid": "2", "date": 20},
            {"gid": "4", "date": 40},
        ]
        self.assertEqual([item["gid"] for item in recent_items(items, 3)], ["2", "3", "4"])

    def test_new_items_filters_and_orders(self):
        items = [
            {"gid": "3", "date": 30},
            {"gid": "1", "date": 10},
            {"gid": "2", "date": 20},
        ]
        self.assertEqual([item["gid"] for item in new_items(items, ["1"])], ["2", "3"])


if __name__ == "__main__":
    unittest.main()
