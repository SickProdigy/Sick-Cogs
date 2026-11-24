import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from grayzonewarfare.grayzonewarfare import GrayZoneWarfare
from grayzonewarfare.announcements import now
from grayzonewarfare.news_client import SteamNewsClient, SteamNewsError


class Value:
    def __init__(self, group, key): self.group=group; self.key=key
    async def __call__(self): return self.group.values[self.key]
    async def set(self, value): self.group.values[self.key]=value


class Group:
    def __init__(self, **values): self.values=dict(GrayZoneWarfare.default_guild, **values)
    async def all(self): return dict(self.values)
    def __getattr__(self, key): return Value(self, key)


def item(gid, date=1):
    return {"gid":gid,"date":date,"feedname":"steam_community_announcements","title":"Patch","url":"https://steamcommunity.com/announcements/detail/123","contents":"[b]Fixes[/b]"}


class NewsTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, group=None):
        group=group or Group(enabled=True,channel_id=9)
        channel=SimpleNamespace(send=AsyncMock(),permissions_for=lambda _:SimpleNamespace(embed_links=True))
        guild=SimpleNamespace(id=1,me=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True)),get_channel_or_thread=lambda _:channel)
        # Permission utility needs guild/member/channel Discord semantics; isolate it here.
        from unittest.mock import patch
        patcher=patch('grayzonewarfare.announcements.can_user_send_messages_in',return_value=True)
        patcher.start();self.addCleanup(patcher.stop)
        cog=GrayZoneWarfare.__new__(GrayZoneWarfare);cog._news_locks={}
        cog.config=SimpleNamespace(guild=lambda _:group)
        cog.bot=SimpleNamespace(guilds=[guild],cog_disabled_in_guild=AsyncMock(return_value=False))
        cog.fetch_news=AsyncMock(return_value=[item('old'),item('new',2)])
        return cog,group,guild,channel

    async def test_success_is_persisted_and_not_repeated(self):
        cog,group,guild,channel=self.make_cog(Group(enabled=True,channel_id=9,posted_ids=['old']))
        self.assertEqual(await cog.poll_news_guild(guild,await cog.fetch_news()),1)
        self.assertEqual(group.values['posted_ids'],['old','new'])
        self.assertEqual(await cog.poll_news_guild(guild,await cog.fetch_news()),0)
        self.assertEqual(channel.send.await_count,1)
        self.assertFalse(channel.send.call_args.kwargs['allowed_mentions'].everyone)

    async def test_failure_leaves_items_pending(self):
        cog,group,guild,channel=self.make_cog();channel.send.side_effect=RuntimeError('failed')
        with self.assertRaises(RuntimeError):await cog.poll_news_guild(guild,[item('new')])
        self.assertEqual(group.values['posted_ids'],[])
        self.assertIsNone(group.values['last_success_at'])

    async def test_disabled_and_not_due_do_not_fetch(self):
        for group in (Group(),Group(enabled=True,channel_id=9,last_poll_at=now().isoformat())):
            cog,_,_,_=self.make_cog(group)
            await cog.poll_news_once();cog.fetch_news.assert_not_awaited()

    async def test_seed_marks_current_without_posting(self):
        cog,group,guild,channel=self.make_cog()
        self.assertEqual(await cog.seed_news(group),2)
        self.assertEqual(group.values['posted_ids'],['old','new']);channel.send.assert_not_awaited()

    async def test_start_seeds_before_enabling(self):
        cog,group,guild,channel=self.make_cog(Group(channel_id=9))
        ctx=SimpleNamespace(guild=guild,send=AsyncMock())
        await GrayZoneWarfare.gzwset_start.callback(cog,ctx)
        self.assertTrue(group.values['enabled']);self.assertEqual(group.values['posted_ids'],['old','new'])
        channel.send.assert_not_awaited()

    async def test_start_provider_failure_keeps_disabled(self):
        cog,group,guild,_=self.make_cog(Group(channel_id=9));cog.fetch_news.side_effect=SteamNewsError('Unavailable')
        ctx=SimpleNamespace(guild=guild,send=AsyncMock())
        await GrayZoneWarfare.gzwset_start.callback(cog,ctx)
        self.assertFalse(group.values['enabled'])

    async def test_stop_and_clear(self):
        cog,group,guild,_=self.make_cog()
        await GrayZoneWarfare.gzwset_channel.callback(cog,SimpleNamespace(guild=guild,send=AsyncMock()),None)
        self.assertFalse(group.values['enabled']);self.assertIsNone(group.values['channel_id'])

    async def test_feed_rejects_third_party(self):
        response=SimpleNamespace(status=200,json=AsyncMock(return_value={'appnews':{'newsitems':[item('yes'),dict(item('no'),feedname='other')]}}))
        context=MagicMock();context.__aenter__=AsyncMock(return_value=response);context.__aexit__=AsyncMock(return_value=False)
        session=SimpleNamespace(get=MagicMock(return_value=context))
        self.assertEqual([i['gid'] for i in await SteamNewsClient(session).fetch()],['yes'])
        self.assertEqual(session.get.call_args.kwargs['params']['appid'],2479810)

    def test_commands_coexist_and_embed_is_bounded(self):
        self.assertIsNotNone(GrayZoneWarfare.grayzonewarfare.get_command('quest'))
        self.assertIsNotNone(GrayZoneWarfare.grayzonewarfare.get_command('updates'))
        self.assertIsNotNone(GrayZoneWarfare.gzwset.get_command('channel'))
        self.assertLessEqual(len(GrayZoneWarfare.news_embed(dict(item('x'),contents='long '*1000)).description),3500)
