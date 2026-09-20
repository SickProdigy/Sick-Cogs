import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nsfw.core import Core

from nsfw.nsfw import Nsfw


class NsfwHelpNameTests(unittest.TestCase):
    def test_cog_help_name_uses_the_legacy_category_name(self):
        self.assertEqual(Nsfw.__cog_name__, "Nsfw")

    def test_placeholder_help_command_is_removed(self):
        self.assertFalse(hasattr(Nsfw, "legacy_nsfw_help"))


class NsfwHelpVisibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_author_and_version_help_footer_is_removed(self):
        self.assertNotIn("format_help_for_context", Core.__dict__)

    async def test_media_is_blocked_before_fetching_outside_age_restricted_channel(self):
        cog = object.__new__(Nsfw)
        cog._make_embed = AsyncMock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(is_nsfw=lambda: False),
            send=AsyncMock(),
        )
        await cog._send_msg(ctx, "test", ["example"])
        self.assertEqual(cog._make_embed.await_count, 0)
        self.assertIn("age-restricted", ctx.send.await_args.args[0])

    async def test_media_fetches_inside_age_restricted_channel(self):
        cog = object.__new__(Nsfw)
        cog._make_embed = AsyncMock(return_value=None)
        cog._maybe_embed = AsyncMock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(is_nsfw=lambda: True),
            send=AsyncMock(),
        )
        await cog._send_msg(ctx, "test", ["example"])
        cog._make_embed.assert_awaited_once_with(ctx, ["example"], "test")
        cog._maybe_embed.assert_awaited_once_with(ctx, embed=None)
