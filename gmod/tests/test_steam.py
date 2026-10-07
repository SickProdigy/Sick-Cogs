import unittest

from gmod.steam import APP_ID, image_url, new_items, plain_text, recent_items


class SteamNewsTests(unittest.TestCase):
    def test_uses_garrys_mod_app_id(self):
        self.assertEqual(APP_ID, 4000)

    def test_plain_text_removes_steam_markup(self):
        source = "[h1]Update[/h1]\n[list][*][b]Fixed[/b] tools &amp; crashes[/list]"
        self.assertEqual(plain_text(source), "Update\n• Fixed tools & crashes")

    def test_image_url_supports_img_src_and_canonicalizes_fastly(self):
        source = (
            "[img src=\"https://clan.fastly.steamstatic.com/"
            "images/123/a.png\"][/img]"
        )
        self.assertEqual(
            image_url(source),
            "https://clan.steamstatic.com/images/123/a.png",
        )

    def test_new_items_filters_and_orders(self):
        items = [
            {"gid": "3", "date": 30},
            {"gid": "1", "date": 10},
            {"gid": "2", "date": 20},
        ]
        self.assertEqual([item["gid"] for item in new_items(items, ["1"])], ["2", "3"])

    def test_recent_items_selects_newest_and_returns_oldest_first(self):
        items = [{"gid": str(value), "date": value} for value in range(1, 6)]
        self.assertEqual([item["gid"] for item in recent_items(items, 3)], ["3", "4", "5"])
