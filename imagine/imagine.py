import asyncio
import io
import time
from typing import Optional
from urllib.parse import urlsplit

import aiohttp
import discord
from redbot.core import Config, checks, commands
from redbot.core.data_manager import cog_data_path

from .access import evaluate_access
from .codex_manager import CodexManager
from .codex_views import CodexSetupView
from .models import ImageRequest
from .providers import CodexImageProvider, ComfyUIProvider, OpenAIImageProvider, ProviderError

CONFIG_IDENTIFIER = 846261450184
CONFIG_SCHEMA_VERSION = 2
OUTPUT_PRESETS = {
    "economy": {"size": "1024x1024", "quality": "low", "background": "auto"},
    "standard": {"size": "1024x1024", "quality": "medium", "background": "auto"},
    "best": {"size": "1024x1024", "quality": "high", "background": "auto"},
    "auto": {"size": "1024x1024", "quality": "auto", "background": "auto"},
}
DEFAULT_GLOBAL = {
    "enabled": True,
    "allowed_guilds": [],
    "openai_enabled": True,
    "codex_enabled": True,
    "comfyui_enabled": False,
    "codex_timeout_seconds": 300,
    "comfyui_endpoint": None,
    "comfyui_workflow": {},
    "schema_version": 1,
}


class Imagine(commands.Cog):
    """Private, provider-neutral image generation."""

    __author__ = ["SickProdigy"]
    __version__ = "0.2.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_IDENTIFIER, force_registration=True)
        self.config.register_global(**DEFAULT_GLOBAL)
        self.config.register_guild(enabled=False, provider="openai", channel_id=None,
                                   allowed_role_ids=[], allowed_user_ids=[],
                                   cooldown_seconds=60, daily_limit=10, model=None,
                                   size="1024x1024", quality="auto", background="auto")
        self.config.register_member(usage_timestamps=[])
        self.session = None
        self._locks = {}
        self._cooldowns = {}
        self.codex_manager = CodexManager(cog_data_path(self) / "codex", lambda: self.session)

    async def cog_load(self):
        schema_version = await self.config.schema_version()
        if schema_version < CONFIG_SCHEMA_VERSION:
            # Codex was originally staged as default-off. Access is already protected by
            # the global guild allowlist, per-guild enablement, grants, and usage limits.
            await self.config.codex_enabled.set(True)
            await self.config.schema_version.set(CONFIG_SCHEMA_VERSION)
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180))

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        for guild_id in await self.config.all_guilds():
            await self.config.member_from_ids(guild_id, user_id).clear()

    async def _access(self, ctx):
        global_data = await self.config.all()
        settings = await self.config.guild(ctx.guild).all()
        decision = evaluate_access(
            globally_enabled=global_data["enabled"],
            guild_allowlisted=ctx.guild.id in global_data["allowed_guilds"],
            guild_enabled=settings["enabled"], user_id=ctx.author.id,
            role_ids=(role.id for role in ctx.author.roles),
            allowed_users=settings["allowed_user_ids"],
            allowed_roles=settings["allowed_role_ids"],
            is_owner=await self.bot.is_owner(ctx.author),
        )
        if decision.allowed and settings["channel_id"] and ctx.channel.id != settings["channel_id"]:
            return False, f"Use image generation in <#{settings['channel_id']}>.", settings, global_data
        return decision.allowed, decision.reason, settings, global_data

    async def _limits(self, ctx, settings):
        now = int(time.time())
        previous = self._cooldowns.get((ctx.guild.id, ctx.author.id), 0)
        if previous + settings["cooldown_seconds"] > now:
            return False, f"Try again in {previous + settings['cooldown_seconds'] - now} seconds."
        cutoff = now - 86400
        conf = self.config.member(ctx.author)
        usage = [stamp for stamp in await conf.usage_timestamps() if stamp > cutoff]
        if settings["daily_limit"] and len(usage) >= settings["daily_limit"]:
            return False, "You have reached this server's daily image limit."
        return True, usage

    async def _provider(self, name, global_data):
        if not self.session:
            raise ProviderError("The image service is still starting.")
        if name == "openai":
            if not global_data["openai_enabled"]:
                raise ProviderError("OpenAI is disabled by the bot owner.")
            tokens = await self.bot.get_shared_api_tokens("openai")
            return OpenAIImageProvider(self.session, tokens.get("api_key", ""))
        if name == "codex":
            if not global_data["codex_enabled"]:
                raise ProviderError("Codex is disabled by the bot owner.")
            executable = self.codex_manager.executable()
            return CodexImageProvider(
                executable=str(executable) if executable else "codex",
                timeout_seconds=global_data["codex_timeout_seconds"],
                codex_home=(self.codex_manager.codex_home
                            if self.codex_manager.bin_path.is_file() else None),
            )
        if name == "comfyui":
            if not global_data["comfyui_enabled"]:
                raise ProviderError("ComfyUI is disabled by the bot owner.")
            return ComfyUIProvider(self.session, global_data["comfyui_endpoint"] or "",
                                   global_data["comfyui_workflow"])
        raise ProviderError("The configured provider is not supported.")

    @commands.group(name="imagine", invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(attach_files=True)
    async def imagine(self, ctx, *, prompt: str = ""):
        """Generate an image, or show status without a prompt."""
        if not prompt:
            await self._status(ctx)
            return
        if len(prompt) > 32000:
            await ctx.send("Prompts may contain at most 32,000 characters.")
            return
        allowed, reason, settings, global_data = await self._access(ctx)
        if not allowed:
            await ctx.send(reason)
            return
        within, usage = await self._limits(ctx, settings)
        if not within:
            await ctx.send(usage)
            return
        lock = self._locks.setdefault(ctx.guild.id, asyncio.Lock())
        if lock.locked():
            await ctx.send("This server's image generator is busy. Try again shortly.")
            return
        request = ImageRequest(prompt, ctx.author.id, ctx.guild.id, settings["size"],
                               settings["quality"], settings["background"], settings["model"])
        async with lock, ctx.typing():
            try:
                result = await (await self._provider(settings["provider"], global_data)).generate(request)
            except ProviderError as exc:
                await ctx.send(str(exc))
                return
            except asyncio.TimeoutError:
                await ctx.send("The image provider timed out.")
                return
        now = int(time.time())
        usage.append(now)
        await self.config.member(ctx.author).usage_timestamps.set(usage)
        self._cooldowns[(ctx.guild.id, ctx.author.id)] = now
        usage = ""
        if result.usage:
            input_tokens = result.usage.get("input_tokens")
            cached_tokens = result.usage.get("cached_input_tokens")
            parts = []
            if isinstance(input_tokens, int):
                fresh_tokens = input_tokens - cached_tokens if isinstance(cached_tokens, int) else input_tokens
                parts.append(f"fresh input: {max(fresh_tokens, 0):,}")
            if isinstance(cached_tokens, int):
                parts.append(f"cached: {cached_tokens:,}")
            for key, label in (("output_tokens", "output"),
                               ("reasoning_output_tokens", "reasoning")):
                if isinstance(result.usage.get(key), int):
                    parts.append(f"{label}: {result.usage[key]:,}")
            if parts:
                usage = "\nCodex turn tokens · " + " · ".join(parts)
        output = f"{settings['size']} · {settings['quality']} quality"
        await ctx.send(f"Generated with **{result.provider}** · `{result.model}`\nOutput: {output}{usage}",
                       file=discord.File(io.BytesIO(result.data), filename=f"imagine-{now}.png"),
                       allowed_mentions=discord.AllowedMentions.none())

    async def _status(self, ctx):
        global_data = await self.config.all()
        settings = await self.config.guild(ctx.guild).all()
        allowed, reason, _, _ = await self._access(ctx)
        channel = f"<#{settings['channel_id']}>" if settings["channel_id"] else "Any channel"
        roles = " ".join(f"<@&{x}>" for x in settings["allowed_role_ids"]) or "None"
        users = " ".join(f"<@{x}>" for x in settings["allowed_user_ids"]) or "None"
        output = {key: settings[key] for key in ("size", "quality", "background")}
        preset = next((name for name, values in OUTPUT_PRESETS.items() if values == output), "custom")
        await ctx.send("**Imagine status**\n"
                       f"Access now: {'Allowed' if allowed else reason}\n"
                       f"Server allowlisted: {'Yes' if ctx.guild.id in global_data['allowed_guilds'] else 'No'}\n"
                       f"Server enabled: {'Yes' if settings['enabled'] else 'No'}\n"
                       f"Provider: `{settings['provider']}`\nChannel: {channel}\n"
                       f"Output: `{preset}` · {settings['size']} · {settings['quality']} quality\n"
                       f"Allowed roles: {roles}\nAllowed users: {users}\n"
                       f"Limits: {settings['daily_limit']} per user/day, {settings['cooldown_seconds']}s cooldown")

    @commands.group(name="imagineset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def imagineset(self, ctx):
        """Configure private Imagine access."""

    @imagineset.command(name="status")
    async def set_status(self, ctx):
        """Show the effective configuration."""
        await self._status(ctx)

    @imagineset.command(name="enable")
    async def set_enable(self, ctx):
        """Enable Imagine after owner approval."""
        if ctx.guild.id not in await self.config.allowed_guilds():
            await ctx.send("The bot owner must allow this server first.")
            return
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Imagine enabled. Grant a user or role before generating.")

    @imagineset.command(name="disable")
    async def set_disable(self, ctx):
        """Disable Imagine in this server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Imagine disabled.")

    @imagineset.command(name="provider")
    async def set_provider(self, ctx, provider: str):
        """Select openai, codex, or comfyui for this server."""
        provider = provider.casefold()
        if provider not in {"openai", "codex", "comfyui"}:
            await ctx.send("Provider must be `openai`, `codex`, or `comfyui`.")
            return
        await self.config.guild(ctx.guild).provider.set(provider)
        await ctx.send(f"Imagine will use `{provider}`.")

    @imagineset.command(name="output")
    async def set_output(self, ctx, preset: Optional[str] = None):
        """Select economy, standard, best, or auto image output."""
        if preset is None:
            settings = await self.config.guild(ctx.guild).all()
            current = {key: settings[key] for key in ("size", "quality", "background")}
            name = next((key for key, values in OUTPUT_PRESETS.items() if values == current), "custom")
            await ctx.send(
                f"Current output: `{name}` · {settings['size']} · {settings['quality']} quality.\n"
                "Choose `economy`, `standard`, `best`, or `auto`."
            )
            return
        preset = preset.casefold()
        if preset not in OUTPUT_PRESETS:
            await ctx.send("Output must be `economy`, `standard`, `best`, or `auto`.")
            return
        values = OUTPUT_PRESETS[preset]
        guild = self.config.guild(ctx.guild)
        await guild.size.set(values["size"])
        await guild.quality.set(values["quality"])
        await guild.background.set(values["background"])
        await ctx.send(
            f"Imagine output set to **{preset}**: {values['size']}, "
            f"{values['quality']} quality."
        )

    @imagineset.command(name="channel")
    async def set_channel(self, ctx, channel: Optional[discord.TextChannel] = None):
        """Set the only allowed channel; omit to clear."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id if channel else None)
        await ctx.send(f"Imagine channel set to {channel.mention}." if channel else "Channel restriction cleared.")

    @imagineset.group(name="role")
    async def set_role(self, ctx):
        """Manage allowed roles."""

    @set_role.command(name="add")
    async def role_add(self, ctx, role: discord.Role):
        """Allow a role."""
        async with self.config.guild(ctx.guild).allowed_role_ids() as values:
            if role.id not in values:
                values.append(role.id)
        await ctx.send(f"{role.mention} may use Imagine.")

    @set_role.command(name="remove")
    async def role_remove(self, ctx, role: discord.Role):
        """Remove an allowed role."""
        async with self.config.guild(ctx.guild).allowed_role_ids() as values:
            if role.id in values:
                values.remove(role.id)
        await ctx.send(f"Removed {role.mention} from Imagine access.")

    @imagineset.group(name="user")
    async def set_user(self, ctx):
        """Manage allowed users."""

    @set_user.command(name="add")
    async def user_add(self, ctx, member: discord.Member):
        """Allow a user."""
        async with self.config.guild(ctx.guild).allowed_user_ids() as values:
            if member.id not in values:
                values.append(member.id)
        await ctx.send(f"{member.mention} may use Imagine.")

    @set_user.command(name="remove")
    async def user_remove(self, ctx, member: discord.Member):
        """Remove an allowed user."""
        async with self.config.guild(ctx.guild).allowed_user_ids() as values:
            if member.id in values:
                values.remove(member.id)
        await ctx.send(f"Removed {member.mention} from Imagine access.")

    @imagineset.command(name="limits")
    async def set_limits(self, ctx, daily: commands.Range[int, 0, 100],
                         cooldown: commands.Range[int, 0, 86400] = 60):
        """Set per-user daily limit (0 unlimited) and cooldown."""
        await self.config.guild(ctx.guild).daily_limit.set(daily)
        await self.config.guild(ctx.guild).cooldown_seconds.set(cooldown)
        await ctx.send(f"Limits set: {daily or 'unlimited'} per day, {cooldown}s cooldown.")

    @imagineset.command(name="model")
    async def set_model(self, ctx, model: str = "gpt-image-1"):
        """Set this server's OpenAI image model."""
        await self.config.guild(ctx.guild).model.set(model.strip())
        await ctx.send(f"Imagine model set to `{model.strip()}`.")

    @imagineset.command(name="global")
    @checks.is_owner()
    async def set_global(self, ctx, enabled: bool):
        """Set the bot-wide emergency generation switch."""
        await self.config.enabled.set(enabled)
        await ctx.send(f"Imagine's global switch is now {'on' if enabled else 'off'}.")

    @imagineset.command(name="providerstate")
    @checks.is_owner()
    async def set_provider_state(self, ctx, provider: str, enabled: bool):
        """Enable or disable an image provider bot-wide."""
        provider = provider.casefold()
        if provider not in {"openai", "codex", "comfyui"}:
            await ctx.send("Provider must be `openai`, `codex`, or `comfyui`.")
            return
        await getattr(self.config, f"{provider}_enabled").set(enabled)
        await ctx.send(f"`{provider}` is now {'enabled' if enabled else 'disabled'} bot-wide.")

    @imagineset.group(name="server")
    @checks.is_owner()
    async def set_server(self, ctx):
        """Bot-owner server allowlist."""

    @set_server.command(name="allow")
    async def server_allow(self, ctx):
        """Allow this server."""
        async with self.config.allowed_guilds() as values:
            if ctx.guild.id not in values:
                values.append(ctx.guild.id)
        await ctx.send("This server is now on Imagine's owner allowlist.")

    @set_server.command(name="remove")
    async def server_remove(self, ctx):
        """Remove and disable this server."""
        async with self.config.allowed_guilds() as values:
            if ctx.guild.id in values:
                values.remove(ctx.guild.id)
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("This server was removed and Imagine was disabled.")

    @imagineset.command(name="comfyui")
    @checks.is_owner()
    async def set_comfyui(self, ctx, endpoint: str):
        """Stage a trusted ComfyUI base URL."""
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            await ctx.send("Use a plain HTTP(S) base URL without credentials, query, or fragment.")
            return
        await self.config.comfyui_endpoint.set(endpoint.rstrip("/"))
        await ctx.send("ComfyUI endpoint staged; workflow mapping is not enabled yet.")

    @imagineset.command(name="codex")
    @checks.is_owner()
    async def set_codex(self, ctx):
        """Open the private Codex install and account-linking controls."""
        version = await self.codex_manager.version()
        installed = f"Installed: `{version}`" if version else "Installed: **No**"
        await ctx.send(
            "**Codex setup**\n"
            f"{installed}\n\n"
            "Install or update the bot-managed CLI, link a ChatGPT account, check the "
            "connection, or disconnect it. Account details and authorization codes are only "
            "shown privately to the bot owner who presses a button.",
            view=CodexSetupView(self, ctx.author.id),
        )
