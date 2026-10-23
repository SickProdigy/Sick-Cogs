import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from mybb.client import MyBBAPIError
from mybb.mybb import MyBB


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def test_link_mode_posts_only_safe_authoritative_url(self):
        cog = object.__new__(MyBB)
        channel = SimpleNamespace(send=AsyncMock())
        await cog.deliver_forum_thread(channel, {
            "thread_id": 2, "thread_url": "https://forum.example/thread-2",
            "subject": "News", "message": "Details",
        }, "link")
        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.await_args.args[0], "https://forum.example/thread-2")
        self.assertEqual(
            channel.send.await_args.kwargs["allowed_mentions"].to_dict(),
            discord.AllowedMentions.none().to_dict(),
        )

    async def test_card_mode_bounds_content_and_links_back(self):
        cog = object.__new__(MyBB)
        channel = SimpleNamespace(send=AsyncMock())
        await cog.deliver_forum_thread(channel, {
            "thread_id": 2, "thread_url": "https://forum.example/thread-2",
            "subject": "S" * 400, "message": "D" * 1000,
            "username": "Author", "forum_name": "Ideas",
        }, "card")
        embed = channel.send.await_args.kwargs["embed"]
        self.assertEqual(len(embed.title), 256)
        self.assertEqual(len(embed.description), 500)
        self.assertEqual(embed.url, "https://forum.example/thread-2")
        self.assertEqual(embed.footer.text, "Read the complete thread on the forum")

    async def test_rejects_non_https_thread_link(self):
        cog = object.__new__(MyBB)
        with self.assertRaises(MyBBAPIError):
            await cog.deliver_forum_thread(
                SimpleNamespace(send=AsyncMock()), {"thread_url": "http://forum.example/1"}, "link"
            )

    def test_thread_ids_are_numeric_and_safe(self):
        self.assertEqual(MyBB._thread_id({"thread_id": "42"}), 42)
        self.assertEqual(MyBB._thread_id({"tid": 7}), 7)
        self.assertEqual(MyBB._thread_id({"thread_id": "invalid"}), 0)


if __name__ == "__main__":
    unittest.main()
