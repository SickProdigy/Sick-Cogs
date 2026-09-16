import json
import unittest

from rocketleague.clips import (
    ClipSourceError,
    _iso_duration_seconds,
    _medal_hydration,
    clip_identity,
    detect_clip_source,
)
from rocketleague.rocketleague import RocketLeague


class ClipSourceTests(unittest.TestCase):
    def test_provider_is_detected_from_supported_urls(self):
        cases = {
            "https://medal.tv/u/strictlyarab": "medal",
            "https://www.twitch.tv/example": "twitch",
            "https://clips.twitch.tv/Example": "twitch",
            "https://www.youtube.com/@example": "youtube",
            "https://youtu.be/abc123": "youtube",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                provider, normalized = detect_clip_source(value)
                self.assertEqual(provider, expected)
                self.assertEqual(normalized, value)

    def test_bare_creator_requires_explicit_provider(self):
        with self.assertRaises(ClipSourceError):
            detect_clip_source("strictlyarab")
        self.assertEqual(
            detect_clip_source("strictlyarab", "medal"),
            ("medal", "strictlyarab"),
        )
        self.assertEqual(detect_clip_source("creator", "yt"), ("youtube", "creator"))

    def test_medal_hydration_is_decoded(self):
        payload = {"profiles": {"abc": {"userId": "abc", "userName": "creator"}}}
        html = f"<script>var hydrationData={json.dumps(payload)}</script>"
        self.assertEqual(_medal_hydration(html), payload)

    def test_iso_duration_conversion(self):
        self.assertEqual(_iso_duration_seconds("PT2M3.5S"), 123.5)
        self.assertEqual(_iso_duration_seconds("PT1H"), 3600)
        self.assertIsNone(_iso_duration_seconds("two minutes"))

    def test_public_medal_metadata_is_normalized(self):
        parser = __import__(
            "rocketleague.clips", fromlist=["_medal_clip_from_html"]
        )._medal_clip_from_html
        html = """
        <meta property="og:title" content="Double tap - Clipped Rocket League with Medal.tv">
        <meta property="og:description" content="Watch Double tap by Player and millions of other Rocket League videos on Medal.">
        <meta property="og:video" content="https://medal.tv/video?v=42.5">
        <meta property="og:image" content="https://example.com/thumb.jpg">
        """
        normalized = parser(
            "https://medal.tv/games/rocket-league/clips/abc123", html
        )
        self.assertEqual(normalized["creator"], "Player")
        self.assertEqual(normalized["duration"], 42.5)
        self.assertEqual(normalized["video_url"], "https://medal.tv/video?v=42.5")
        self.assertEqual(normalized["title"], "Double tap")
        self.assertEqual(
            normalized["url"],
            "https://medal.tv/games/rocket-league/clips/abc123",
        )

    def test_twitch_channel_and_clip_urls_are_parsed(self):
        parser = __import__("rocketleague.clips", fromlist=["ClipProviders"]).ClipProviders._twitch_name
        self.assertEqual(
            parser("https://www.twitch.tv/rocketleague/clips?range=30d"),
            ("rocketleague", None),
        )
        self.assertEqual(
            parser("https://clips.twitch.tv/ExampleClip"),
            (None, "ExampleClip"),
        )

    def test_source_rotation_advances_by_server_id(self):
        candidates = [
            ({"id": 1, "name": "Medal"}, [{"clip_id": "m", "views": 1}]),
            ({"id": 2, "name": "Twitch"}, [{"clip_id": "t", "views": 1}]),
            ({"id": 3, "name": "YouTube"}, [{"clip_id": "y", "views": 1}]),
        ]
        ordered = sorted(candidates, key=lambda item: int(item[0].get("id") or 0))
        for last_source, expected in ((1, 2), (2, 3), (3, 1)):
            selected, _ = next(
                (item for item in ordered if int(item[0].get("id") or 0) > last_source),
                ordered[0],
            )
            self.assertEqual(selected["id"], expected)

    def test_highest_view_clip_is_preferred(self):
        selected = RocketLeague._preferred_clip(
            [
                {"clip_id": "low", "views": 2},
                {"clip_id": "high", "views": 200},
                {"clip_id": "middle", "views": 20},
            ]
        )
        self.assertEqual(selected["clip_id"], "high")

    def test_clip_identity_is_provider_scoped(self):
        self.assertEqual(
            clip_identity({"provider": "medal", "clip_id": "abc"}),
            "medal:abc",
        )

    def test_admin_clip_commands_are_registered(self):
        clips = RocketLeague.rocketleagueset.get_command("clips")
        self.assertIsNotNone(clips)
        self.assertIn("clip", clips.aliases)
        for name in (
            "channel",
            "sourceadd",
            "sourceremove",
            "sources",
            "interval",
            "maxlength",
            "enable",
            "disable",
            "status",
            "refresh",
            "postnow",
        ):
            self.assertIsNotNone(clips.get_command(name), name)

    def test_medal_server_promotions_are_rejected(self):
        parser = __import__(
            "rocketleague.clips", fromlist=["_medal_clip_from_html"]
        )._medal_clip_from_html
        html = """
        <meta property="og:title" content="Join my Discord server - Clipped Rocket League with Medal.tv">
        <meta property="og:description" content="Watch this by Player and millions of other Rocket League videos on Medal.">
        <meta property="og:video" content="https://medal.tv/video?v=42">
        """
        self.assertIsNone(
            parser("https://medal.tv/games/rocket-league/clips/promo", html)
        )

    def test_normalized_clip_keeps_canonical_provider_url(self):
        parser = __import__(
            "rocketleague.clips", fromlist=["_medal_clip_from_html"]
        )._medal_clip_from_html
        html = """
        <meta property="og:title" content="Ceiling shot - Clipped Rocket League with Medal.tv">
        <meta property="og:description" content="Watch this by Player and millions of other Rocket League videos on Medal.">
        <meta property="og:video" content="https://medal.tv/video?v=65">
        """
        clip = parser("https://medal.tv/games/rocket-league/clips/example", html)
        self.assertEqual(clip["url"], "https://medal.tv/games/rocket-league/clips/example")


if __name__ == "__main__":
    unittest.main()
