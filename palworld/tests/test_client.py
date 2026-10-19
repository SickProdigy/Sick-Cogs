import unittest
from unittest.mock import AsyncMock, Mock, patch

from palworld import setup
from palworld.palworld import CONFIG_IDENTIFIER, Palworld

from palworld.client import (
    SteamNewsClient,
    SteamNewsError,
    classify_news,
    extract_image,
    extract_images,
    extract_youtube_urls,
    image_dimensions,
    is_official_news,
    optimize_gallery_image,
    new_items,
    plain_text,
    recent_items,
)


class PalworldClientTests(unittest.TestCase):
    def test_updates_support_positional_cards_and_compact_list(self):
        updates = Palworld.palworld.get_command("updates")
        self.assertIn("position", updates.clean_params)
        self.assertIn("count", updates.get_command("list").clean_params)

    def test_public_help_includes_quick_setup(self):
        help_text = Palworld.palworld.help
        self.assertIn("[p]palworldset channel #updates", help_text)
        self.assertIn("[p]palworldset mode card", help_text)
        self.assertIn("[p]palworldset autopost start", help_text)

    def test_classifies_primary_categories(self):
        cases = [
            ("Server Hotfix", "hotfixes"),
            ("Emergency Server Maintenance", "maintenance"),
            ("Summer Raid Event Begins", "events"),
            ("v1.0.4: Balance Adjustments & Bug Fixes", "updates"),
            ("ULTRAKILL x Palworld Collab coming soon", "releases"),
            ("Palworld Steam Bundle is Out Now", "promotions"),
            ("Palworld Global Popularity Poll", "community"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_news({"title": title, "contents": ""}), expected)

    def test_official_feed_filter_excludes_third_party_news(self):
        self.assertTrue(is_official_news({"feedname": "steam_community_announcements"}))
        self.assertFalse(is_official_news({"feedname": "PCGamesN"}))

    def test_unknown_is_official(self):
        self.assertEqual(classify_news({"title": "Creature spotlight", "contents": "Details"}), "official")

    def test_plain_text_removes_steam_markup(self):
        source = "[h1]Patch Notes[/h1]\n[list][*][color=#fff][b]Fixed[/b][/color] bugs &amp; crashes.[/list]\n[img]https://example.com/a.png[/img]"
        self.assertEqual(plain_text(source), "Patch Notes\n• Fixed bugs & crashes.")

    def test_youtube_preview_becomes_url_and_not_stale_text(self):
        source = "Before\n[previewyoutube=gsWm02GX1zw;full]TRAILER[/previewyoutube]\nAfter"
        self.assertEqual(extract_youtube_urls(source), ["https://youtu.be/gsWm02GX1zw"])
        self.assertEqual(plain_text(source), "Before\n\nAfter")
        self.assertEqual(
            plain_text(source, youtube_links=True),
            "Before\nWatch trailer on YouTube: https://youtu.be/gsWm02GX1zw\nAfter",
        )

    def test_extracts_palworld_img_src_markup(self):
        source = (
            "[p][img src=\"{STEAM_CLAN_IMAGE}/4458811/"
            "3affd440d2688d4b46e6878268f71cf0c065a0f4.png\"][/img][/p]"
        )
        expected = (
            "https://clan.steamstatic.com/images/4458811/"
            "3affd440d2688d4b46e6878268f71cf0c065a0f4.png"
        )
        self.assertEqual(extract_image(source), expected)
        self.assertEqual(extract_images(source), [expected])

    def test_fastly_clan_image_is_canonicalized(self):
        source = (
            "[img src=\"https://clan.fastly.steamstatic.com/"
            "images/123/a.png\"][/img]"
        )
        self.assertEqual(
            extract_image(source),
            "https://clan.steamstatic.com/images/123/a.png",
        )

    def test_extracts_and_expands_steam_clan_image(self):
        source = "[img]{STEAM_CLAN_IMAGE}/12345/banner.jpg[/img]"
        self.assertEqual(
            extract_image(source),
            "https://clan.steamstatic.com/images/12345/banner.jpg",
        )

    def test_reads_png_dimensions_for_gallery_filtering(self):
        payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 8) + (2560).to_bytes(4, "big") + (1440).to_bytes(4, "big")
        self.assertEqual(image_dimensions(payload), (2560, 1440))

    def test_gallery_image_is_resized_and_encoded_as_jpeg(self):
        from PIL import Image
        from io import BytesIO

        source = BytesIO()
        Image.new("RGB", (2560, 1440), "#556b2f").save(source, format="PNG")
        result = optimize_gallery_image(source.getvalue())
        with Image.open(BytesIO(result)) as resized:
            self.assertEqual(resized.format, "JPEG")
            self.assertEqual(resized.size, (1600, 900))
        self.assertLess(len(result), len(source.getvalue()))

    def test_card_uses_first_image_in_steam_order(self):
        source = (
            "[img]{STEAM_CLAN_IMAGE}/12345/header.png[/img]\n"
            "Introduction\n"
            "[img]{STEAM_CLAN_IMAGE}/12345/featured.jpg[/img]\n"
            "[url=https://drive.google.com/example][color=#3498db]"
            "Download in full resolution[/color][/url]"
        )
        self.assertEqual(
            extract_image(source),
            "https://clan.steamstatic.com/images/12345/header.png",
        )

    def test_recent_release_artwork_uses_direct_steam_cdn(self):
        source = (
            "[img]{STEAM_CLAN_IMAGE}/44719856/"
            "14decb75883b079ef427a162df1a3649fe9cefc6.png[/img]\n"
            "[previewyoutube=gsWm02GX1zw;full]TRAILER[/previewyoutube]\n"
            "[img]{STEAM_CLAN_IMAGE}/44719856/"
            "674a529bc710eb4218cb2ec321a4d5eb99776c33.png[/img]\n"
            "[url=https://drive.google.com/example][color=#3498db]"
            "Download all screenshots in full resolution[/color][/url]"
        )
        self.assertEqual(
            extract_image(source),
            "https://clan.steamstatic.com/images/44719856/"
            "14decb75883b079ef427a162df1a3649fe9cefc6.png",
        )
        self.assertEqual(
            extract_images(source),
            [
                "https://clan.steamstatic.com/images/44719856/"
                "14decb75883b079ef427a162df1a3649fe9cefc6.png",
                "https://clan.steamstatic.com/images/44719856/"
                "674a529bc710eb4218cb2ec321a4d5eb99776c33.png",
            ],
        )

    def test_redirecting_legacy_cdn_is_canonicalized(self):
        source = (
            "[img]https://clan.cloudflare.steamstatic.com/images/"
            "12345/banner.png[/img]"
        )
        self.assertEqual(
            extract_image(source),
            "https://clan.steamstatic.com/images/12345/banner.png",
        )

    def test_plain_text_omits_full_resolution_link_and_extra_blank_line(self):
        source = (
            "First paragraph.\n\n"
            "[url=https://drive.google.com/example][color=#3498db]"
            "Download in full resolution[/color][/url]\n\n"
            "Second paragraph."
        )
        self.assertEqual(plain_text(source), "First paragraph.\n\nSecond paragraph.")

    def test_plain_text_omits_all_screenshots_full_resolution_link(self):
        source = (
            "First paragraph.\n"
            "[url=https://drive.google.com/example][color=#3498db]"
            "Download all screenshots in full resolution[/color][/url]"
        )
        self.assertEqual(plain_text(source), "First paragraph.")

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


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class SteamNewsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_palworld_app_and_keeps_only_official_items(self):
        session = _Session(_Response({"appnews": {"newsitems": [
            {"gid": "official", "feedname": "steam_community_announcements"},
            {"gid": "external", "feedname": "PC Gamer"},
            {"feedname": "steam_community_announcements"},
        ]}}))
        items = await SteamNewsClient(session).fetch(count=500)
        self.assertEqual([item["gid"] for item in items], ["official"])
        params = session.calls[0][1]["params"]
        self.assertEqual(params["appid"], 1623730)
        self.assertEqual(params["count"], 100)

    async def test_fetch_rejects_http_and_malformed_responses(self):
        with self.assertRaises(SteamNewsError):
            await SteamNewsClient(_Session(_Response({}, status=429))).fetch()
        with self.assertRaises(SteamNewsError):
            await SteamNewsClient(_Session(_Response({"appnews": {"newsitems": {}}}))).fetch()


class PalworldSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_uses_new_namespace_and_starts_loop_on_load(self):
        bot = Mock(add_cog=AsyncMock())
        config = Mock()
        with (
            patch("palworld.palworld.Config.get_conf", return_value=config) as get_conf,
            patch("discord.ext.tasks.Loop.start") as start,
        ):
            await setup(bot)
            cog = bot.add_cog.await_args.args[0]
            await cog.cog_load()
        self.assertIsInstance(cog, Palworld)
        get_conf.assert_called_once_with(
            cog, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
