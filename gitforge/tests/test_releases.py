import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from gitforge.gitforge import GitForge
from gitforge.providers import ForgeError
from gitforge.views import RepositoryModal
from gitforge.tests.test_browsing import Client


def raw_release(number=1, **overrides):
    return {"id": number, "tag_name": f"v{number}", "name": f"Release {number}",
            "body": "Notes", "description": "Notes", "published_at": f"2026-01-{number:02}T00:00:00Z",
            "released_at": f"2026-01-{number:02}T00:00:00Z",
            "html_url": "https://forge.example.com/o/r/releases/1",
            "_links": {"self": "https://forge.example.com/o/r/releases/1"}, **overrides}


def release(number):
    return {"id": str(number), "tag": f"v{number}", "name": f"Release {number}",
            "body": "Notes", "published": f"2026-01-{number:02}T00:00:00+00:00",
            "url": "https://forge.example.com/o/r/releases/1"}


class ReleaseProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_matrix_filters_and_orders(self):
        for provider in ("gitea", "github", "gitlab"):
            rows = [raw_release(2), raw_release(1), raw_release(3, draft=True),
                    raw_release(4, prerelease=True), raw_release(5, upcoming_release=True),
                    raw_release(6, released_at="2099-01-01T00:00:00Z", published_at="2099-01-01T00:00:00Z")]
            client = Client(provider, [rows])
            items = await client.list_releases("o", "r")
            self.assertEqual([item["tag"] for item in items], ["v1", "v2"])
            self.assertEqual(items[0]["id"], "v1" if provider == "gitlab" else "1")
            params = client.calls[0][2]["params"]
            self.assertEqual(params["limit" if provider == "gitea" else "per_page"], 50)
            self.assertEqual(params["page"], 1)
            self.assertEqual(client.calls[0][1], "/projects/o%2Fr/releases" if provider == "gitlab" else "/repos/o/r/releases")
            if provider == "gitea":
                self.assertEqual(params["draft"], "false")
            if provider == "gitlab":
                self.assertEqual(params["order_by"], "released_at")

    async def test_empty_success_but_malformed_or_denied_is_error(self):
        self.assertEqual(await Client("github", [[]]).list_releases("o", "r"), [])
        for payload in ({}, [None], [{}], [raw_release(id=None)],
                        [raw_release(released_at="bad")], [raw_release(released_at="2026-01-01")],
                        [raw_release(_links={}, html_url="https://evil.example/a")],
                        [raw_release()] * 51, ForgeError("missing", status=404),
                        ForgeError("rate limited", status=429)):
            with self.subTest(payload=payload), self.assertRaises(ForgeError):
                await Client("github", [payload]).list_releases("o", "r")

    async def test_duplicate_records_and_edits_keep_stable_identity(self):
        client = Client("gitlab", [[raw_release(), raw_release(name="Edited notes")]])
        items = await client.list_releases("o", "r")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "v1")


class Value:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.set = AsyncMock(side_effect=self.save)

    async def __call__(self):
        return copy.deepcopy(self.value)

    async def save(self, value):
        self.value = copy.deepcopy(value)


class Config:
    def __init__(self, repositories):
        self.groups = {int(i): SimpleNamespace(repositories=Value(data),
                       connections=Value({"forge": {"provider": "github", "base_url": "https://forge.example.com"}}))
                       for i, data in repositories.items()}

    def guild(self, guild):
        return self.groups[guild.id]

    async def all_guilds(self):
        return {i: {"repositories": await group.repositories()} for i, group in self.groups.items()}


def repository(channel=10, initialized=False, seen=None):
    return {"connection": "forge", "owner": "o", "repo": "r", "release_channel_id": channel,
            "releases_initialized": initialized, "seen_release_ids": seen or [],
            "issue_channel_id": 0, "ci_channel_id": 0}


class ReleasePollingTests(unittest.IsolatedAsyncioTestCase):
    def setup_cog(self, repositories=None):
        cog = object.__new__(GitForge)
        cog.poll_lock = asyncio.Lock()
        cog._poll_backoff = {}
        cog.config = Config(repositories or {1: {"project": repository()}})
        channel = SimpleNamespace(id=10, mention="<#10>", send=AsyncMock(),
                    permissions_for=lambda member: SimpleNamespace(view_channel=True, send_messages=True, embed_links=True))
        guilds = {i: SimpleNamespace(id=i, me=object(), get_channel=lambda cid: channel if cid == 10 else None)
                  for i in cog.config.groups}
        cog.bot = SimpleNamespace(get_guild=lambda gid: guilds.get(gid))
        client = SimpleNamespace(provider="github", base_url="https://forge.example.com", token="test",
                    list_releases=AsyncMock(return_value=[release(1)]), list_issues=AsyncMock(return_value=[]),
                    list_ci_runs=AsyncMock(return_value=[]), repository=AsyncMock(return_value={}))
        cog.client_for = AsyncMock(return_value=client)
        return cog, channel, client, guilds

    async def test_seed_then_new_releases_oldest_first_and_restart_safe(self):
        cog, channel, client, guilds = self.setup_cog()
        await cog.poll_once()
        channel.send.assert_not_awaited()
        client.list_releases.return_value = [release(1), release(2), release(3)]
        await cog.poll_once()
        self.assertEqual([call.kwargs["embed"].fields[0].value for call in channel.send.await_args_list], ["v2", "v3"])
        for call in channel.send.await_args_list:
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        cog._poll_backoff = {}  # Persisted cursor still applies after reload.
        await cog.poll_once()
        self.assertEqual(channel.send.await_count, 2)

    async def test_empty_baseline_still_announces_first_release(self):
        cog, channel, client, _ = self.setup_cog()
        client.list_releases.return_value = []
        await cog.poll_once()
        client.list_releases.return_value = [release(1)]
        await cog.poll_once()
        channel.send.assert_awaited_once()

    async def test_partial_failure_retains_success_and_retries_only_pending(self):
        cog, channel, client, _ = self.setup_cog({1: {"project": repository(initialized=True)}})
        client.list_releases.return_value = [release(1), release(2)]
        channel.send.side_effect = [None, discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")]
        with self.assertLogs("red.sickcogs.gitforge", level="WARNING"):
            await cog.poll_once()
        saved = await cog.config.groups[1].repositories()
        self.assertEqual(saved["project"]["seen_release_ids"], ["1"])
        channel.send.reset_mock(side_effect=True)
        await cog.poll_once()
        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.call_args.kwargs["embed"].fields[0].value, "v2")

    async def test_missing_channel_keeps_pending_releases(self):
        cog, channel, client, _ = self.setup_cog({1: {"project": repository(channel=99, initialized=True)}})
        await cog.poll_once()
        saved = await cog.config.groups[1].repositories()
        self.assertEqual(saved["project"]["seen_release_ids"], [])
        channel.send.assert_not_awaited()

    async def test_grouping_and_disabled_routes(self):
        cog, channel, client, _ = self.setup_cog({1: {"a": repository()}, 2: {"b": repository()},
                                               3: {"disabled": repository(channel=0)}})
        await cog.poll_once()
        client.list_releases.assert_awaited_once()
        self.assertTrue((await cog.config.groups[1].repositories())["a"]["releases_initialized"])
        self.assertTrue((await cog.config.groups[2].repositories())["b"]["releases_initialized"])
        self.assertFalse((await cog.config.groups[3].repositories())["disabled"]["releases_initialized"])

    async def test_release_failure_backoff_does_not_block_other_routes(self):
        row = repository()
        row.update(issue_channel_id=10, ci_channel_id=10)
        cog, channel, client, _ = self.setup_cog({1: {"project": row}})
        client.list_releases.side_effect = ForgeError("rate limited", status=429)
        await cog.poll_once()
        await cog.poll_once()
        self.assertEqual(client.list_releases.await_count, 1)
        self.assertEqual(client.list_issues.await_count, 2)
        self.assertEqual(client.list_ci_runs.await_count, 2)
        saved = (await cog.config.groups[1].repositories())["project"]
        self.assertFalse(saved["releases_initialized"])
        self.assertTrue(saved["issues_initialized"])

    async def test_channel_commands_preserve_history_and_enforce_permissions(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(initialized=True, seen=["1"])}})
        ctx = SimpleNamespace(guild=guilds[1], send=AsyncMock())
        await GitForge.release_channel.callback(cog, ctx, "project", None)
        await cog.poll_once()
        client.list_releases.assert_not_awaited()
        await GitForge.release_channel.callback(cog, ctx, "project", channel)
        saved = (await cog.config.groups[1].repositories())["project"]
        self.assertEqual(saved["seen_release_ids"], ["1"])
        self.assertTrue(saved["releases_initialized"])
        embed = await cog.settings_embed(guilds[1])
        self.assertIn("releases: <#10>", embed.fields[1].value)
        channel.permissions_for = lambda member: SimpleNamespace(view_channel=True, send_messages=False, embed_links=True)
        with self.assertRaises(ValueError):
            await GitForge.release_channel.callback(cog, ctx, "project", channel)

    async def test_cursor_and_embed_bounds(self):
        cog, channel, client, guilds = self.setup_cog()
        row = repository(initialized=True, seen=[str(i) for i in range(200)])
        item = release(999)
        item.update(name="@everyone" * 100, body="@everyone" * 1000)
        await cog._poll_releases(guilds[1], "project", row, [item])
        self.assertEqual(len(row["seen_release_ids"]), 200)
        embed = channel.send.call_args.kwargs["embed"]
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed.description), 3000)

    async def test_repository_modal_preserves_same_backend_and_reseeds_changed_backend(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(initialized=True, seen=["1"])}})
        view = SimpleNamespace(cog=cog, show_error=AsyncMock())
        interaction = SimpleNamespace(guild=guilds[1], user=SimpleNamespace(id=5, guild_permissions=SimpleNamespace(manage_guild=True)),
                response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()), edit_original_response=AsyncMock())
        for repo, initialized in (("o/r", True), ("o/other", False)):
            modal = RepositoryModal(view)
            modal.alias._value = "project"
            modal.connection._value = "forge"
            modal.repository._value = repo
            with patch("gitforge.views.SetupView", return_value=None):
                await modal.on_submit(interaction)
            saved = (await cog.config.groups[1].repositories())["project"]
            self.assertEqual(saved["releases_initialized"], initialized)
            self.assertEqual(saved["seen_release_ids"], ["1"] if initialized else [])
            self.assertEqual(saved["release_channel_id"], 10)


    async def test_channel_change_waits_for_poll_and_preserves_completed_cursor(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(initialized=True)}})
        started, finish = asyncio.Event(), asyncio.Event()
        async def fetch(owner, repo):
            started.set()
            await finish.wait()
            return [release(1)]
        client.list_releases.side_effect = fetch
        async def polling():
            async with cog.poll_lock:
                await cog.poll_once()
        poll = asyncio.create_task(polling())
        await started.wait()
        ctx = SimpleNamespace(guild=guilds[1], send=AsyncMock())
        change = asyncio.create_task(GitForge.release_channel.callback(cog, ctx, "project", None))
        await asyncio.sleep(0)
        self.assertFalse(change.done())
        finish.set()
        await asyncio.gather(poll, change)
        saved = (await cog.config.groups[1].repositories())["project"]
        self.assertEqual(saved["seen_release_ids"], ["1"])
        self.assertEqual(saved["release_channel_id"], 0)

    async def test_distinct_credentials_do_not_share_release_requests(self):
        cog, _, client, _ = self.setup_cog({1: {"a": repository()}, 2: {"b": repository()}})
        other = copy.copy(client)
        other.token = "different"
        other.list_releases = AsyncMock(return_value=[])
        cog.client_for.side_effect = lambda guild, connection: client if guild.id == 1 else other
        await cog.poll_once()
        client.list_releases.assert_awaited_once()
        other.list_releases.assert_awaited_once()

    async def test_one_guild_delivery_failure_does_not_block_other_guild(self):
        cog, _, client, guilds = self.setup_cog({1: {"a": repository(initialized=True)},
                                               2: {"b": repository(initialized=True)}})
        broken = SimpleNamespace(send=AsyncMock(side_effect=discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "denied")))
        guilds[1].get_channel = lambda cid: broken
        with self.assertLogs("red.sickcogs.gitforge", level="WARNING"):
            await cog.poll_once()
        self.assertEqual((await cog.config.groups[1].repositories())["a"]["seen_release_ids"], [])
        self.assertEqual((await cog.config.groups[2].repositories())["b"]["seen_release_ids"], ["1"])
