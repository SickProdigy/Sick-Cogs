import unittest

from ark.ark import ArkAnnouncements

from ark.client import (
    classify_news,
    extract_image,
    extract_images,
    extract_youtube_urls,
    image_dimensions,
    optimize_gallery_image,
    new_items,
    plain_text,
    recent_items,
)


class ArkClientTests(unittest.TestCase):
    def test_public_help_includes_quick_setup(self):
        help_text = ArkAnnouncements.ark.help
        self.assertIn("[p]arkset channel #updates", help_text)
        self.assertIn("[p]arkset mode card", help_text)
        self.assertIn("[p]arkset autopost start", help_text)

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

    def test_youtube_preview_becomes_url_and_not_stale_text(self):
        source = "Before\n[previewyoutube=gsWm02GX1zw;full]TRAILER[/previewyoutube]\nAfter"
        self.assertEqual(extract_youtube_urls(source), ["https://youtu.be/gsWm02GX1zw"])
        self.assertEqual(plain_text(source), "Before\n\nAfter")
        self.assertEqual(
            plain_text(source, youtube_links=True),
            "Before\nWatch trailer on YouTube: https://youtu.be/gsWm02GX1zw\nAfter",
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
        Image.new("RGB", (2560, 1440), "#315f4c").save(source, format="PNG")
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


if __name__ == "__main__":
    unittest.main()
