import asyncio
import json
import secrets
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp
import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import box, humanize_list, pagify


class AzerothCore(commands.Cog):
    """Interact with an AzerothCore server through a configurable REST bridge."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(self, 4528967103, force_registration=True)
        self.config.register_global(
            server_name=None,
            realmlist=None,
            info_description=None,
            # SOAP-only configuration (defaults to a local network example)
            use_soap=True,
            soap_url="http://192.168.1.1:17878/",
            soap_user=None,
            soap_pass=None,
            soap_envelope_template=(
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<soap:Envelope xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\">\n"
                "  <soap:Body>\n"
                "    <Execute>{command}</Execute>\n"
                "  </soap:Body>\n"
                "</soap:Envelope>"
            ),
            soap_create_command_template="account create {username} {password}",
            soap_info_command_template="server info",
            soap_online_command_template="players",
            request_timeout=20,
        )
        self.config.register_guild(allowed_roles=[])

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())

    def _build_url(self, base_url: str, path: str) -> str:
        base = base_url if base_url.endswith("/") else f"{base_url}/"
        return base + path.lstrip("/")

    def _render_template(self, value: Any, **replacements: Any) -> Any:
        if isinstance(value, str):
            return value.format(**replacements)
        if isinstance(value, list):
            return [self._render_template(item, **replacements) for item in value]
        if isinstance(value, dict):
            return {key: self._render_template(item, **replacements) for key, item in value.items()}
        return value

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[Any], Optional[str]]:
        # REST transport removed; SOAP-only mode uses `_soap_execute` instead.
        return None, "REST transport is disabled in this configuration. Use SOAP settings instead."

    async def _soap_execute(self, command: str) -> Tuple[Optional[str], Optional[str]]:
        """Execute a SOAP envelope against the configured SOAP endpoint and return the inner text."""
        use_soap = await self.config.use_soap()
        if not use_soap:
            return None, "SOAP is not enabled."

        soap_url = await self.config.soap_url()
        if not soap_url:
            return None, "SOAP URL has not been configured. Use `ac set soap_url` to set it."

        envelope_template = await self.config.soap_envelope_template()
        body = envelope_template.format(command=command)

        headers = {"Content-Type": "text/xml; charset=utf-8", "Accept": "text/xml"}
        soap_user = await self.config.soap_user()
        soap_pass = await self.config.soap_pass()
        auth = None
        if soap_user:
            auth = aiohttp.BasicAuth(soap_user, soap_pass or "")

        try:
            timeout = aiohttp.ClientTimeout(total=await self.config.request_timeout())
            async with self.session.post(soap_url, data=body.encode(), headers=headers, timeout=timeout, auth=auth) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return None, f"SOAP request failed with HTTP {resp.status}: {text[:300]}"

                text = await resp.text()
                # Try to extract <Execute>...</Execute> content, otherwise strip tags naively
                import re

                m = re.search(r"<Execute[^>]*>(.*?)</Execute>", text, flags=re.S | re.I)
                if m:
                    return m.group(1).strip(), None

                stripped = re.sub(r"<[^>]+>", "", text).strip()
                return stripped, None
        except asyncio.TimeoutError:
            return None, "The SOAP request timed out."
        except Exception as exc:
            return None, f"SOAP request error: {exc}"

    def _first_value(self, data: Any, keys: Iterable[str], default: Any = None) -> Any:
        if not isinstance(data, dict):
            return default
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        return default

    def _parse_server_info(self, text: Optional[str]) -> Dict[str, Any]:
        """Parse common server info fields from console output into a dict."""
        out = (text or "").strip()
        result: Dict[str, Any] = {}
        if not out:
            return result

        import re

        # Name/Realm
        m = re.search(r"^(?:Server|Realm|Name):\s*(.+)$", out, flags=re.I | re.M)
        if m:
            result["name"] = m.group(1).strip()

        # Version
        m = re.search(r"Version:\s*(.+)$", out, flags=re.I | re.M)
        if m:
            result["version"] = m.group(1).strip()

        # Uptime
        m = re.search(r"Uptime:\s*(.+)$", out, flags=re.I | re.M)
        if m:
            result["uptime"] = m.group(1).strip()

        # Players online
        m = re.search(r"Players\s*online:\s*(\d+)", out, flags=re.I)
        if not m:
            m = re.search(r"Online:\s*(\d+)", out, flags=re.I)
        if m:
            try:
                result["online"] = int(m.group(1))
            except Exception:
                result["online"] = m.group(1)

        # Max players / capacity
        m = re.search(r"(Max|Maximum|Capacity):\s*(\d+)", out, flags=re.I)
        if m:
            try:
                result["max_players"] = int(m.group(2))
            except Exception:
                result["max_players"] = m.group(2)

        # Expansion
        m = re.search(r"Expansion:\s*(.+)$", out, flags=re.I | re.M)
        if m:
            result["expansion"] = m.group(1).strip()

        # Description / message block
        m = re.search(r"(?:Description|Message|Details):\s*(.+)$", out, flags=re.I | re.M)
        if m:
            result["description"] = m.group(1).strip()
        else:
            # fallback: first non-empty line after a header
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            if lines:
                result.setdefault("description", lines[0])

        return result

    def _parse_players(self, text: Optional[str]) -> List[Dict[str, Any]]:
        """Parse player list console output into list of player dicts.

        Heuristics: look for lines with 'Name:' and 'Level:', otherwise fallback to first token as name.
        """
        out = (text or "").strip()
        if not out:
            return []

        players: List[Dict[str, Any]] = []
        import re

        for line in out.splitlines():
            ln = line.strip()
            if not ln:
                continue

            player: Dict[str, Any] = {"raw": ln}

            # Pattern: Name: Alice  Level: 60  Class: Mage  Zone: Stormwind
            m = re.search(r"Name:\s*(?P<name>\S+)\b.*Level:\s*(?P<level>\d+).*Class:\s*(?P<class>\S+).*Zone:\s*(?P<zone>.+)$", ln, flags=re.I)
            if not m:
                m = re.search(r"Name:\s*(?P<name>\S+)\b.*Level:\s*(?P<level>\d+).*Class:\s*(?P<class>\S+)", ln, flags=re.I)
            if m:
                player["name"] = m.group("name")
                player["level"] = int(m.group("level")) if m.group("level").isdigit() else m.group("level")
                if "class" in m.groupdict():
                    player["class"] = m.group("class")
                if "zone" in m.groupdict():
                    player["zone"] = m.group("zone").strip()
                players.append(player)
                continue

            # Simple 'Name Level Zone' column format - try split
            parts = ln.split()
            if parts:
                # assume first token is name
                player["name"] = parts[0]
                # try to find a numeric token as level
                for token in parts[1:]:
                    if token.isdigit():
                        player["level"] = int(token)
                        break
                players.append(player)
                continue

            players.append({"raw": ln})

        return players

    def _format_online_entry(self, entry: Any) -> str:
        if isinstance(entry, str):
            return entry

        if not isinstance(entry, dict):
            return str(entry)

        name = self._first_value(entry, ("name", "character_name", "character", "player", "username"), "Unknown")
        level = self._first_value(entry, ("level", "lvl", "character_level"), None)
        race = self._first_value(entry, ("race", "race_name"), None)
        char_class = self._first_value(entry, ("class", "class_name", "player_class"), None)
        guild = self._first_value(entry, ("guild", "guild_name"), None)

        details: List[str] = []
        if level is not None:
            details.append(f"lvl {level}")
        if race:
            details.append(str(race))
        if char_class:
            details.append(str(char_class))
        if guild:
            details.append(f"guild {guild}")

        if details:
            return f"{name} ({', '.join(details)})"
        return str(name)

    def _extract_online_list(self, data: Any) -> List[Any]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []

        for key in ("players", "characters", "online", "results", "data", "members"):
            value = data.get(key)
            if isinstance(value, list):
                return value

        return []

    async def _can_create_accounts(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False

        if ctx.guild.owner_id == ctx.author.id:
            return True

        if ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild:
            return True

        allowed_role_ids = await self.config.guild(ctx.guild).allowed_roles()
        if not allowed_role_ids:
            return False

        member_roles = {role.id for role in ctx.author.roles}
        return any(role_id in member_roles for role_id in allowed_role_ids)

    def _can_embed(self, ctx: commands.Context) -> bool:
        channel = ctx.channel
        if hasattr(channel, "permissions_for") and ctx.me is not None:
            return channel.permissions_for(ctx.me).embed_links
        return True

    async def _send_embed_or_text(self, ctx: commands.Context, embed: discord.Embed, text: str) -> None:
        if self._can_embed(ctx):
            await ctx.send(embed=embed)
        else:
            await ctx.send(text)

    @commands.group(name="ac", aliases=("azerothcore",), invoke_without_command=True)
    async def ac(self, ctx: commands.Context):
        """AzerothCore server commands."""

        await ctx.send_help()

    @ac.command(name="info")
    async def ac_info(self, ctx: commands.Context):
        """Show general server information."""
        # Prefer SOAP if enabled
        if await self.config.use_soap():
            cmd_template = await self.config.soap_info_command_template()
            cmd = self._render_template(cmd_template)
            out, error = await self._soap_execute(cmd)
            if error:
                return await ctx.send(error)

            info = self._parse_server_info(out)

            title = info.get("name") or await self.config.server_name() or "AzerothCore"
            embed = discord.Embed(title=title, color=await ctx.embed_color())
            configured_realmlist = await self.config.realmlist()
            if configured_realmlist:
                embed.add_field(name="Realmlist", value=str(configured_realmlist), inline=False)

            if info.get("online") is not None and info.get("max_players") is not None:
                embed.add_field(name="Population", value=f"{info.get('online')}/{info.get('max_players')}", inline=True)
            elif info.get("online") is not None:
                embed.add_field(name="Online", value=str(info.get("online")), inline=True)

            if info.get("version"):
                embed.add_field(name="Version", value=str(info.get("version")), inline=True)
            if info.get("expansion"):
                embed.add_field(name="Expansion", value=str(info.get("expansion")), inline=True)
            if info.get("uptime"):
                embed.add_field(name="Uptime", value=str(info.get("uptime")), inline=True)

            description = info.get("description") or (await self.config.info_description()) or "Server information fetched successfully."
            embed.description = str(description)[:1900]

            fallback = [f"{title}"]
            if configured_realmlist:
                fallback.append(f"Realmlist: {configured_realmlist}")
            if info.get("online") is not None and info.get("max_players") is not None:
                fallback.append(f"Population: {info.get('online')}/{info.get('max_players')}")
            elif info.get("online") is not None:
                fallback.append(f"Online: {info.get('online')}")
            if info.get("version"):
                fallback.append(f"Version: {info.get('version')}")
            if info.get("expansion"):
                fallback.append(f"Expansion: {info.get('expansion')}")
            if info.get("uptime"):
                fallback.append(f"Uptime: {info.get('uptime')}")

            await self._send_embed_or_text(ctx, embed, "\n".join(fallback))
            return

        # Fallback to REST bridge
        data, error = await self._request_json("GET", await self.config.status_path())
        if error:
            return await ctx.send(error)

        if isinstance(data, str):
            return await ctx.send(f"```text\n{data[:1900]}\n```")

        if not isinstance(data, dict):
            return await ctx.send("The server returned an unexpected response.")

        configured_name = await self.config.server_name()
        configured_realmlist = await self.config.realmlist()
        configured_description = await self.config.info_description()

        name = configured_name or self._first_value(data, ("name", "server_name", "realm_name", "realm", "title"), "AzerothCore")
        status = self._first_value(data, ("status", "state", "online", "is_online"), None)
        online = self._first_value(data, ("online_players", "online_count", "players_online", "population"), None)
        maximum = self._first_value(data, ("max_players", "max_count", "players_max", "capacity"), None)
        version = self._first_value(data, ("version", "build", "realm_version"), None)
        expansion = self._first_value(data, ("expansion", "expansion_name"), None)
        uptime = self._first_value(data, ("uptime", "uptime_human", "uptime_text"), None)

        embed = discord.Embed(title=str(name), color=await ctx.embed_color())
        if configured_realmlist:
            embed.add_field(name="Realmlist", value=str(configured_realmlist), inline=False)
        if status is not None:
            embed.add_field(name="Status", value=str(status), inline=True)
        if online is not None and maximum is not None:
            embed.add_field(name="Population", value=f"{online}/{maximum}", inline=True)
        elif online is not None:
            embed.add_field(name="Online", value=str(online), inline=True)
        if version is not None:
            embed.add_field(name="Version", value=str(version), inline=True)
        if expansion is not None:
            embed.add_field(name="Expansion", value=str(expansion), inline=True)
        if uptime is not None:
            embed.add_field(name="Uptime", value=str(uptime), inline=True)

        description = self._first_value(
            data,
            ("description", "message", "details", "summary"),
            configured_description or "Server information fetched successfully.",
        )
        embed.description = str(description)

        fallback = [f"{name}"]
        if configured_realmlist:
            fallback.append(f"Realmlist: {configured_realmlist}")
        if status is not None:
            fallback.append(f"Status: {status}")
        if online is not None and maximum is not None:
            fallback.append(f"Population: {online}/{maximum}")
        elif online is not None:
            fallback.append(f"Online: {online}")
        if version is not None:
            fallback.append(f"Version: {version}")
        if expansion is not None:
            fallback.append(f"Expansion: {expansion}")
        if uptime is not None:
            fallback.append(f"Uptime: {uptime}")

        await self._send_embed_or_text(ctx, embed, "\n".join(fallback))

    @ac.command(name="online")
    async def ac_online(self, ctx: commands.Context):
        """Show the characters currently online."""
        # Use SOAP to get online players
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")

        cmd_template = await self.config.soap_online_command_template()
        cmd = self._render_template(cmd_template)
        out, error = await self._soap_execute(cmd)
        if error:
            return await ctx.send(error)

        players = self._parse_players(out)
        if not players:
            return await ctx.send("No online player information returned.")

        formatted = []
        for p in players:
            if "name" in p:
                details: List[str] = []
                if p.get("level") is not None:
                    details.append(f"lvl {p['level']}")
                if p.get("class"):
                    details.append(str(p.get("class")))
                if p.get("zone"):
                    details.append(str(p.get("zone")))
                if details:
                    formatted.append(f"{p['name']} ({', '.join(details)})")
                else:
                    formatted.append(p["name"])
            else:
                formatted.append(p.get("raw", ""))

        header = f"Currently online: {len(formatted)} player{'s' if len(formatted) != 1 else ''}"
        embed = discord.Embed(title="Online Characters", description=header, color=await ctx.embed_color())

        if self._can_embed(ctx):
            pages = list(pagify("\n".join(formatted), delims=["\n"], page_length=900))
            if len(pages) == 1:
                embed.add_field(name="Characters", value=pages[0], inline=False)
                await ctx.send(embed=embed)
                return

            await ctx.send(embed=embed)
            for page in pages:
                await ctx.send(page)
            return

        await ctx.send(header + "\n" + "\n".join(formatted))

    @ac.command(name="createuser", aliases=("createaccount",))
    @commands.guild_only()
    async def ac_createuser(self, ctx: commands.Context, username: str, email: Optional[str] = None):
        """Create a new game account through the configured API bridge."""

        if not await self._can_create_accounts(ctx):
            return await ctx.send("You do not have permission to create accounts.")
        # Use SOAP to create account
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")

        password = secrets.token_urlsafe(12)
        template = await self.config.soap_create_command_template()
        cmd = self._render_template(template, username=username, password=password, email=email or "")
        out, error = await self._soap_execute(cmd)
        if error:
            return await ctx.send(error)

        lower_out = (out or "").lower()
        if "created" in lower_out or "success" in lower_out or "account" in (out or ""):
            try:
                await ctx.author.send(
                    "Your AzerothCore account was created.\n"
                    f"Username: {username}\n"
                    f"Password: {password}\n"
                )
                await ctx.send("Account created. I sent the login details to you in DMs.")
            except discord.Forbidden:
                await ctx.send(
                    "Account created, but I could not DM you the password. Please enable DMs and try again if needed."
                )
            return

        # Helpful hints
        if "exists" in lower_out or "already" in lower_out:
            hint = "Account may already exist."
        elif "permission" in lower_out or "denied" in lower_out:
            hint = "SOAP access denied — check SOAP credentials and server permissions."
        else:
            hint = None

        msg = "Failed to create account."
        if hint:
            msg += f" {hint}"
        msg += f" Raw response: {out[:1000]}"
        await ctx.send(msg)

    @ac.group(name="set", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset(self, ctx: commands.Context):
        """Configure the AzerothCore bridge and permissions."""

        message = (
            "AzerothCore settings (SOAP-only):\n"
            "- `ac set soap_url <url>`\n"
            "- `ac set soap_auth <user> <pass>`\n"
            "- `ac set servername <name>`\n"
            "- `ac set realmlist <text>`\n"
            "- `ac set infodescription <text>`\n"
            "- `ac set accountcreationrole <role...>`\n"
            "- `ac set roleremove <role...>`\n"
            "- `ac set rolelist`\n"
            "- `ac set view`\n"
            "Example: `ac set soap_url http://soapuser:pass@192.168.1.1:7878/`"
        )
        await ctx.send(message)

    @acset.command(name="baseurl")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_baseurl(self, ctx: commands.Context, base_url: str):
        """Set the base URL for the AzerothCore bridge."""
        # REST transport removed; no-op
        await ctx.send("Base URL is managed by SOAP-only configuration. Use `ac set soap_url`.")

    @acset.command(name="servername")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_servername(self, ctx: commands.Context, *, server_name: Optional[str] = None):
        """Set or clear the display name used by the info command."""

        if not server_name:
            await self.config.server_name.clear()
            await ctx.send("Server name cleared.")
            return

        await self.config.server_name.set(server_name)
        await ctx.send(f"Server name set to {server_name}")

    @acset.command(name="realmlist")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_realmlist(self, ctx: commands.Context, *, realmlist: Optional[str] = None):
        """Set or clear the realmlist shown to users."""

        if not realmlist:
            await self.config.realmlist.clear()
            await ctx.send("Realmlist cleared.")
            return

        await self.config.realmlist.set(realmlist)
        await ctx.send(f"Realmlist set to {realmlist}")

    @acset.command(name="infodescription")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_infodescription(self, ctx: commands.Context, *, info_description: Optional[str] = None):
        """Set or clear the default description shown by the info command."""

        if not info_description:
            await self.config.info_description.clear()
            await ctx.send("Info description cleared.")
            return

        await self.config.info_description.set(info_description)
        await ctx.send("Info description updated.")

    @acset.command(name="token")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_token(self, ctx: commands.Context, token: Optional[str] = None):
        """Set or clear the bearer token used for requests."""
        # REST transport removed; no-op
        await ctx.send("Token is not used in SOAP-only mode. Use `ac set soap_auth` to configure SOAP credentials.")

    @acset.command(name="statuspath")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_statuspath(self, ctx: commands.Context, path: str):
        """Set the status endpoint path."""
        # REST transport removed; no-op
        await ctx.send("Status path is not used in SOAP-only mode.")

    @acset.command(name="onlinepath")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_onlinepath(self, ctx: commands.Context, path: str):
        """Set the online players endpoint path."""
        # REST transport removed; no-op
        await ctx.send("Online path is not used in SOAP-only mode.")

    @acset.command(name="createpath")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createpath(self, ctx: commands.Context, path: str):
        """Set the account creation endpoint path."""
        # REST transport removed; no-op
        await ctx.send("Create path is not used in SOAP-only mode.")

    @acset.command(name="createmethod")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createmethod(self, ctx: commands.Context, method: str):
        """Set the HTTP method used for account creation."""
        # REST transport removed; no-op
        await ctx.send("Create method is not used in SOAP-only mode.")

    @acset.command(name="createbody")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createbody(self, ctx: commands.Context, *, create_body: Optional[str] = None):
        """Set or clear the JSON payload template used to create accounts."""
        # REST transport removed; no-op
        await ctx.send("Create body JSON template is not used in SOAP-only mode.")

    @acset.command(name="accountcreationrole", aliases=("roleadd",))
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_accountcreationrole(self, ctx: commands.Context, *roles: discord.Role):
        """Allow the provided roles to create accounts."""

        if not roles:
            return await ctx.send("You need to provide at least one role.")

        async with self.config.guild(ctx.guild).allowed_roles() as allowed_roles:
            for role in roles:
                if role.id not in allowed_roles:
                    allowed_roles.append(role.id)

        await ctx.send(f"Allowed roles updated: {humanize_list(role.mention for role in roles)}")

    @acset.command(name="roleremove")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_roleremove(self, ctx: commands.Context, *roles: discord.Role):
        """Remove roles from the account-creation allowlist."""

        if not roles:
            return await ctx.send("You need to provide at least one role.")

        removed: List[discord.Role] = []
        async with self.config.guild(ctx.guild).allowed_roles() as allowed_roles:
            for role in roles:
                if role.id in allowed_roles:
                    allowed_roles.remove(role.id)
                    removed.append(role)

        if removed:
            await ctx.send(f"Removed: {humanize_list(role.mention for role in removed)}")
        else:
            await ctx.send("None of the provided roles were on the allowlist.")

    @acset.command(name="rolelist")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_rolelist(self, ctx: commands.Context):
        """Show the roles allowed to create accounts."""

        allowed_roles = await self.config.guild(ctx.guild).allowed_roles()
        if not allowed_roles:
            return await ctx.send("No roles are currently allowed to create accounts.")

        role_mentions = []
        for role_id in allowed_roles:
            role = ctx.guild.get_role(role_id)
            if role is not None:
                role_mentions.append(role.mention)

        if not role_mentions:
            return await ctx.send("No configured roles still exist in this guild.")

        await ctx.send(f"Roles allowed to create accounts: {humanize_list(role_mentions)}")

    @acset.command(name="soap_url")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_soap_url(self, ctx: commands.Context, soap_url: Optional[str] = None):
        """Set or clear the SOAP endpoint URL."""

        if not soap_url:
            await self.config.soap_url.clear()
            return await ctx.send("SOAP URL cleared.")

        await self.config.soap_url.set(soap_url)
        await ctx.send(f"SOAP URL set to {soap_url}")

    @acset.command(name="soap_auth")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_soap_auth(self, ctx: commands.Context, user: Optional[str] = None, password: Optional[str] = None):
        """Set or clear SOAP basic auth credentials. Run with no args to clear."""

        if not user:
            await self.config.soap_user.clear()
            await self.config.soap_pass.clear()
            return await ctx.send("SOAP credentials cleared.")

        await self.config.soap_user.set(user)
        await self.config.soap_pass.set(password or "")
        await ctx.send("SOAP credentials updated.")

    @acset.command(name="view")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_view(self, ctx: commands.Context):
        """Show the current bridge configuration."""
        server_name = await self.config.server_name()
        realmlist = await self.config.realmlist()
        info_description = await self.config.info_description()
        soap_url = await self.config.soap_url()
        soap_user = await self.config.soap_user()
        soap_envelope = await self.config.soap_envelope_template()
        soap_create = await self.config.soap_create_command_template()
        soap_online = await self.config.soap_online_command_template()
        allowed_roles = await self.config.guild(ctx.guild).allowed_roles()

        embed = discord.Embed(title="AzerothCore Configuration (SOAP)", color=await ctx.embed_color())
        embed.add_field(name="SOAP URL", value=soap_url or "Not set", inline=False)
        embed.add_field(name="SOAP Auth", value=("Set" if soap_user else "Not set"), inline=True)
        embed.add_field(name="Server Name", value=server_name or "Not set", inline=True)
        embed.add_field(name="Realmlist", value=realmlist or "Not set", inline=True)
        embed.add_field(name="Info Description", value=info_description or "Not set", inline=False)
        embed.add_field(name="SOAP Create Cmd", value=box(soap_create, lang=""), inline=False)
        embed.add_field(name="SOAP Online Cmd", value=box(soap_online, lang=""), inline=False)

        role_mentions = []
        for role_id in allowed_roles:
            role = ctx.guild.get_role(role_id)
            if role is not None:
                role_mentions.append(role.mention)

        embed.add_field(name="Allowed Roles", value=humanize_list(role_mentions) if role_mentions else "None", inline=False)
        await ctx.send(embed=embed)