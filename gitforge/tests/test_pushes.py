import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from gitforge.gitforge import GitForge
from gitforge.providers import ForgeError, normalize_branch
from gitforge.tests import test_releases
from gitforge.tests.test_browsing import Client
from gitforge.views import RepositoryModal


OLD = "a" * 40
NEW = "b" * 40
URL = "https://forge.example.com/o/r/commit/" + NEW


def snapshot(sha=NEW):
    return {"sha": sha, "url": URL, "message": "@everyone **New work**\nDetails"}


def repository(branches=None, heads=None, channel=10):
    return {"connection": "forge", "owner": "o", "repo": "r", "push_channel_id": channel,
            "push_branches": ["main"] if branches is None else branches,
            "push_heads": {} if heads is None else heads}


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_branch_provider_matrix_and_encoded_names(self):
        for provider in ("gitea", "github", "gitlab"):
            commit = {"id": NEW, "sha": NEW, "url": URL, "web_url": URL, "html_url": URL,
                      "message": "New work", "commit": {"message": "New work"}}
            client = Client(provider, [{"name": "feature/test", "commit": commit}])
            result = await client.branch_snapshot("team/sub", "r", "feature/test")
            self.assertEqual(result, {"sha": NEW, "url": URL, "message": "New work"})
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0][0], "GET")
            self.assertEqual(client.calls[0][1], "/projects/team%2Fsub%2Fr/repository/branches/feature%2Ftest"
                             if provider == "gitlab" else "/repos/team%2Fsub/r/branches/feature%2Ftest")

    async def test_bad_data_denied_deleted_and_redirect_fail_closed(self):
        valid = {"name": "main", "commit": {"sha": NEW, "html_url": URL, "commit": {"message": "New"}}}
        values = [[], {}, {**valid, "name": "other"}, {**valid, "commit": None},
                  {**valid, "commit": {**valid["commit"], "sha": "bad"}},
                  {**valid, "commit": {**valid["commit"], "html_url": "https://evil.example/a"}}]
        values += [ForgeError("failed", status=code) for code in (301, 403, 404, 429, 500)]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ForgeError):
                await Client("github", [value]).branch_snapshot("o", "r", "main")

    async def test_branch_validation_precedes_network(self):
        for branch in ("", "*", "main~1", "HEAD^", "a..b", "a//b", "/main", "a/", ".a", "a/.b",
                       "a.lock", "a?b", "a\\b", "a b", "a\nb", "@{1}", "@", "-a", "a.", "x" * 201):
            client = Client("gitea", [])
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                await client.branch_snapshot("o", "r", branch)
            self.assertEqual(client.calls, [])
        self.assertEqual(normalize_branch("Feature/test-1"), "Feature/test-1")


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def setup_cog(self, repos=None):
        cog, channel, client, guilds = test_releases.ReleasePollingTests.setup_cog(
            self, repos or {1: {"project": repository()}})
        client.branch_snapshot = AsyncMock(return_value=snapshot())
        return cog, channel, client, guilds

    async def test_baseline_change_restart_and_reset(self):
        cog, channel, client, _ = self.setup_cog()
        client.branch_snapshot.return_value = snapshot(OLD)
        await cog.poll_once()
        channel.send.assert_not_awaited()
        client.branch_snapshot.return_value = snapshot()
        await cog.poll_once()
        cog._poll_backoff = {}
        await cog.poll_once()
        channel.send.assert_awaited_once()
        embed = channel.send.call_args.kwargs["embed"]
        self.assertEqual(embed.fields[0].value, OLD[:12])
        self.assertEqual(embed.fields[1].value, NEW[:12])
        self.assertNotIn("@everyone", embed.description)
        self.assertEqual(channel.send.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        # A reset to an earlier SHA is still an observable branch change.
        client.branch_snapshot.return_value = snapshot(OLD)
        await cog.poll_once()
        self.assertEqual(channel.send.await_count, 2)

    async def test_no_channel_or_selection_means_no_fetch(self):
        for repo in (repository(channel=0), repository(branches=[]), {"connection": "forge", "owner": "o", "repo": "r"}):
            cog, channel, client, _ = self.setup_cog({1: {"project": repo}})
            await cog.poll_once()
            client.branch_snapshot.assert_not_awaited()
            channel.send.assert_not_awaited()

    async def test_failed_and_missing_destination_preserve_pending_head(self):
        for channel_id in (10, 99):
            cog, channel, client, _ = self.setup_cog({1: {"project": repository(heads={"main": OLD}, channel=channel_id)}})
            channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
            if channel_id == 10:
                with self.assertLogs("red.sickcogs.gitforge", level="WARNING"):
                    await cog.poll_once()
            else:
                await cog.poll_once()
            saved = await cog.config.groups[1].repositories()
            self.assertEqual(saved["project"]["push_heads"], {"main": OLD})
            saved["project"]["push_channel_id"] = 10
            await cog.config.groups[1].repositories.set(saved)
            channel.send.side_effect = None
            await cog.poll_once()
            self.assertEqual((await cog.config.groups[1].repositories())["project"]["push_heads"], {"main": NEW})

    async def test_branch_jobs_are_grouped_and_failures_isolated(self):
        cog, channel, client, _ = self.setup_cog({1: {"one": repository(["main", "broken"], {"main": OLD}),
                                                    "two": repository(heads={"main": OLD})}})
        async def fetch(owner, repo, branch):
            if branch == "broken":
                raise ForgeError("missing", status=404)
            return snapshot()
        client.branch_snapshot.side_effect = fetch
        await cog.poll_once()
        self.assertEqual(client.branch_snapshot.await_count, 2)
        self.assertEqual(channel.send.await_count, 2)
        await cog.poll_once()
        self.assertEqual(client.branch_snapshot.await_count, 3)  # broken branch backs off independently.
        saved = await cog.config.groups[1].repositories()
        self.assertEqual(saved["one"]["push_heads"], {"main": NEW})

    async def test_partial_delivery_does_not_repeat_successful_branch(self):
        cog, channel, client, _ = self.setup_cog({1: {"project": repository(["main", "develop"], {"main": OLD, "develop": OLD})}})
        channel.send.side_effect = [None, discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")]
        with self.assertLogs("red.sickcogs.gitforge", level="WARNING"):
            await cog.poll_once()
        self.assertEqual((await cog.config.groups[1].repositories())["project"]["push_heads"], {"main": NEW, "develop": OLD})
        channel.send.side_effect = None
        await cog.poll_once()
        self.assertEqual(channel.send.await_count, 3)
        self.assertIn("develop", channel.send.call_args.kwargs["embed"].title)

    async def test_distinct_credentials_not_shared(self):
        cog, channel, client, _ = self.setup_cog({1: {"one": repository()}, 2: {"two": repository()}})
        other = copy.copy(client)
        other.token = "different"
        other.branch_snapshot = AsyncMock(return_value=snapshot(OLD))
        cog.client_for.side_effect = lambda guild, connection: client if guild.id == 1 else other
        await cog.poll_once()
        client.branch_snapshot.assert_awaited_once()
        other.branch_snapshot.assert_awaited_once()
        self.assertEqual((await cog.config.groups[2].repositories())["two"]["push_heads"], {"main": OLD})

    async def test_selection_is_atomic_bounded_case_sensitive_and_preserves_retained_heads(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(heads={"main": OLD})}})
        ctx = SimpleNamespace(guild=guilds[1], send=AsyncMock())
        client.branch_snapshot.side_effect = [snapshot(), ForgeError("missing", status=404)]
        with self.assertRaises(ForgeError):
            await GitForge.push_branches.callback(cog, ctx, "project", "main", "bad")
        self.assertEqual((await cog.config.groups[1].repositories())["project"]["push_branches"], ["main"])
        client.branch_snapshot.side_effect = None
        with self.assertRaises(ValueError):
            await GitForge.push_branches.callback(cog, ctx, "project", *list("abcdef"))
        await GitForge.push_branches.callback(cog, ctx, "project", "main", "main", "Main")
        saved = (await cog.config.groups[1].repositories())["project"]
        self.assertEqual(saved["push_branches"], ["main", "Main"])
        self.assertEqual(saved["push_heads"], {"main": OLD})
        await GitForge.push_branches.callback(cog, ctx, "project")
        saved = (await cog.config.groups[1].repositories())["project"]
        self.assertEqual(saved["push_branches"], [])
        self.assertEqual(saved["push_heads"], {})

    async def test_pause_and_channel_permission_checks(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(heads={"main": OLD})}})
        ctx = SimpleNamespace(guild=guilds[1], send=AsyncMock())
        await GitForge.push_channel.callback(cog, ctx, "project", None)
        await cog.poll_once()
        client.branch_snapshot.assert_not_awaited()
        await GitForge.push_channel.callback(cog, ctx, "project", channel)
        await cog.poll_once()
        channel.send.assert_awaited_once()
        channel.permissions_for = lambda member: SimpleNamespace(view_channel=True, send_messages=True, embed_links=False)
        with self.assertRaises(ValueError):
            await GitForge.push_channel.callback(cog, ctx, "project", channel)

    async def test_reregister_preserves_state_backend_change_reseeds(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(heads={"main": OLD})}})
        view = SimpleNamespace(cog=cog, show_error=AsyncMock())
        interaction = SimpleNamespace(guild=guilds[1], user=SimpleNamespace(id=5, guild_permissions=SimpleNamespace(manage_guild=True)),
                    response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()), edit_original_response=AsyncMock())
        for name, heads in (("o/r", {"main": OLD}), ("o/other", {})):
            modal = RepositoryModal(view)
            modal.alias._value, modal.connection._value, modal.repository._value = "project", "forge", name
            with patch("gitforge.views.SetupView", return_value=None):
                await modal.on_submit(interaction)
            saved = (await cog.config.groups[1].repositories())["project"]
            self.assertEqual(saved["push_heads"], heads)
            self.assertEqual(saved["push_branches"], ["main"])
            self.assertEqual(saved["push_channel_id"], 10)

    async def test_configuration_waits_for_active_poll(self):
        cog, channel, client, guilds = self.setup_cog({1: {"project": repository(heads={"main": OLD})}})
        started, finish = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            started.set()
            await finish.wait()
            return snapshot()
        client.branch_snapshot.side_effect = fetch
        async def poll():
            async with cog.poll_lock:
                await cog.poll_once()
        polling = asyncio.create_task(poll())
        await started.wait()
        ctx = SimpleNamespace(guild=guilds[1], send=AsyncMock())
        change = asyncio.create_task(GitForge.push_branches.callback(cog, ctx, "project"))
        await asyncio.sleep(0)
        self.assertFalse(change.done())
        finish.set()
        await asyncio.gather(polling, change)
        saved = (await cog.config.groups[1].repositories())["project"]
        self.assertEqual(saved["push_branches"], [])
        self.assertEqual(saved["push_heads"], {})

    async def test_setup_lists_route_and_selection(self):
        cog, channel, client, guilds = self.setup_cog()
        embed = await cog.settings_embed(guilds[1])
        repos = next(field.value for field in embed.fields if field.name == "Repositories")
        self.assertIn("pushes: <#10> (main)", repos)

    async def test_branch_fetch_concurrency_is_bounded(self):
        repos = {1: {f"r{i}": {**repository(), "repo": f"r{i}"} for i in range(12)}}
        cog, channel, client, _ = self.setup_cog(repos)
        active = peak = 0
        async def fetch(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return snapshot()
        client.branch_snapshot.side_effect = fetch
        await cog.poll_once()
        self.assertEqual(client.branch_snapshot.await_count, 12)
        self.assertLessEqual(peak, 6)
        self.assertGreater(peak, 1)

    async def test_large_messages_stay_within_discord_limits(self):
        cog, channel, client, _ = self.setup_cog({1: {"project": repository(heads={"main": OLD})}})
        client.branch_snapshot.return_value = {**snapshot(), "message": "*" * 5000}
        await cog.poll_once()
        embed = channel.send.call_args.kwargs["embed"]
        self.assertLessEqual(len(embed.description), 3000)
        self.assertLessEqual(len(embed), 6000)
