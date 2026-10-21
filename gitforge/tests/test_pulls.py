import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from gitforge.gitforge import GitForge
from gitforge.providers import ForgeError
from gitforge.tests.test_browsing import Client
from gitforge.tests import test_releases
from gitforge.views import RepositoryModal


def raw(number=1, **changes):
    return {"number": number, "iid": number, "state": "open", "title": "Change",
            "updated_at": "2026-01-01T00:00:00Z",
            "html_url": "https://forge.example.com/o/r/pulls/1",
            "web_url": "https://forge.example.com/o/r/-/merge_requests/1", **changes}


def item(number=1, state="open"):
    return {"id": str(number), "state": state, "title": "@everyone change",
            "kind": "Pull request", "url": "https://forge.example.com/o/r/pulls/1"}


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_matrix(self):
        for provider in ("gitea", "github", "gitlab"):
            client = Client(provider, [[raw(2, state="merged" if provider == "gitlab" else "closed",
                                           merged_at="2026-01-01T00:00:00Z"),
                                       raw(state="opened" if provider == "gitlab" else "open")]])
            results = await client.list_pulls("o", "r")
            self.assertEqual([(i["id"], i["state"]) for i in results], [("1", "open"), ("2", "merged")])
            params = client.calls[0][2]["params"]
            self.assertEqual(params["state"], "all")
            self.assertEqual(params["limit" if provider == "gitea" else "per_page"], 50)
            self.assertEqual(params["sort"], {"gitea": "recentupdate", "github": "updated", "gitlab": "desc"}[provider])
            self.assertTrue(client.calls[0][1].endswith("/merge_requests" if provider == "gitlab" else "/pulls"))

    async def test_closed_is_not_merged(self):
        for provider in ("gitea", "github", "gitlab"):
            rows = await Client(provider, [[raw(state="closed")]]).list_pulls("o", "r")
            self.assertEqual(rows[0]["state"], "closed")

    async def test_invalid_or_failed_response_never_becomes_empty_baseline(self):
        for value in ({}, [None], [raw(number=True)], [raw(state="unknown")],
                      [raw(updated_at="2026-01-01")], [raw(html_url="https://evil.example/a")],
                      [raw()] * 51, ForgeError("denied", status=403)):
            with self.subTest(value=value), self.assertRaises(ForgeError):
                await Client("github", [value]).list_pulls("o", "r")
        self.assertEqual(await Client("github", [[]]).list_pulls("o", "r"), [])


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def setup_cog(self, initialized=False, states=None, channel=10):
        cog, destination, client, guilds = test_releases.ReleasePollingTests.setup_cog(self, {1: {"project": {
            "connection": "forge", "owner": "o", "repo": "r", "pr_channel_id": channel,
            "prs_initialized": initialized, "pr_states": states or {}}}})
        client.list_pulls = AsyncMock(return_value=[item()])
        return cog, destination, client, guilds

    async def test_seed_transitions_edits_and_reload(self):
        cog, channel, client, _ = self.setup_cog()
        await cog.poll_once()
        channel.send.assert_not_awaited()
        for state in ("closed", "open", "merged"):
            client.list_pulls.return_value = [item(state=state)]
            await cog.poll_once()
        self.assertIn("reopened", channel.send.await_args_list[1].kwargs["embed"].title)
        client.list_pulls.return_value[0]["title"] = "Edited"
        cog._poll_backoff = {}
        await cog.poll_once()
        self.assertEqual(channel.send.await_count, 3)
        self.assertEqual(channel.send.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_empty_seed_then_new_request(self):
        cog, channel, client, _ = self.setup_cog()
        client.list_pulls.return_value = []
        await cog.poll_once()
        client.list_pulls.return_value = [item()]
        await cog.poll_once()
        channel.send.assert_awaited_once()

    async def test_repository_registration_preserves_or_reseeds(self):
        cog, channel, client, guilds = self.setup_cog(True, {"1": "open"})
        view = SimpleNamespace(cog=cog, show_error=AsyncMock())
        interaction = SimpleNamespace(guild=guilds[1], user=SimpleNamespace(id=5, guild_permissions=SimpleNamespace(manage_guild=True)),
                    response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()), edit_original_response=AsyncMock())
        for name, initialized in (("o/r", True), ("o/other", False)):
            modal = RepositoryModal(view)
            modal.alias._value = "project"
            modal.connection._value = "forge"
            modal.repository._value = name
            with patch("gitforge.views.SetupView", return_value=None):
                await modal.on_submit(interaction)
            saved = (await cog.config.groups[1].repositories())["project"]
            self.assertEqual(saved["prs_initialized"], initialized)
            self.assertEqual(saved["pr_states"], {"1": "open"} if initialized else {})
            self.assertEqual(saved["pr_channel_id"], 10)

    async def test_aliases_share_fetch_but_keep_independent_state(self):
        cog, channel, client, _ = self.setup_cog(True)
        repos = await cog.config.groups[1].repositories()
        repos["second"] = {**repos["project"], "pr_states": {"1": "open"}}
        await cog.config.groups[1].repositories.set(repos)
        await cog.poll_once()
        client.list_pulls.assert_awaited_once()
        channel.send.assert_awaited_once()

    async def test_partial_failure_retries_only_pending(self):
        cog, channel, client, _ = self.setup_cog(True)
        client.list_pulls.return_value = [item(), item(2)]
        channel.send.side_effect = [None, discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")]
        with self.assertLogs("red.sickcogs.gitforge", level="WARNING"):
            await cog.poll_once()
        self.assertEqual((await cog.config.groups[1].repositories())["project"]["pr_states"], {"1": "open"})
        channel.send.side_effect = None
        await cog.poll_once()
        self.assertEqual(channel.send.await_count, 3)
        self.assertIn("#2", channel.send.call_args.kwargs["embed"].title)

    async def test_disabled_and_missing_channel(self):
        for channel_id in (0, 99):
            cog, channel, client, _ = self.setup_cog(True, channel=channel_id)
            await cog.poll_once()
            channel.send.assert_not_awaited()
            self.assertEqual((await cog.config.groups[1].repositories())["project"]["pr_states"], {})
            if channel_id == 0:
                client.list_pulls.assert_not_awaited()

    async def test_failure_backoff_preserves_baseline(self):
        cog, channel, client, _ = self.setup_cog()
        client.list_pulls.side_effect = ForgeError("limited", status=429)
        await cog.poll_once()
        await cog.poll_once()
        client.list_pulls.assert_awaited_once()
        self.assertFalse((await cog.config.groups[1].repositories())["project"]["prs_initialized"])

    async def test_history_bounded(self):
        cog, channel, client, _ = self.setup_cog(True, {str(i): "open" for i in range(1, 201)})
        client.list_pulls.return_value = [item(201)]
        await cog.poll_once()
        states = (await cog.config.groups[1].repositories())["project"]["pr_states"]
        self.assertEqual(len(states), 200)
        self.assertNotIn("1", states)

    async def test_command_pause_resume_retains_history_and_checks_permissions(self):
        cog, channel, client, guilds = self.setup_cog(True, {"1": "open"})
        ctx = SimpleNamespace(guild=guilds[1], send=AsyncMock())
        await GitForge.pr_channel.callback(cog, ctx, "project", None)
        await GitForge.pr_channel.callback(cog, ctx, "project", channel)
        self.assertEqual((await cog.config.groups[1].repositories())["project"]["pr_states"], {"1": "open"})
        channel.permissions_for = lambda member: SimpleNamespace(view_channel=True, send_messages=False, embed_links=True)
        with self.assertRaises(ValueError):
            await GitForge.pr_channel.callback(cog, ctx, "project", channel)
