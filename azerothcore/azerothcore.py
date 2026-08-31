import asyncio
import errno
import html
import secrets
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import box, humanize_list, pagify


LEGACY_PLACEHOLDER_SOAP_URL = "http://192.168.1.1:17878/"
LEGACY_SOAP_ENVELOPE_TEMPLATE = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    "<soap:Envelope xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\">\n"
    "  <soap:Body>\n"
    "    <Execute>{command}</Execute>\n"
    "  </soap:Body>\n"
    "</soap:Envelope>"
)
DEFAULT_SOAP_ENVELOPE_TEMPLATE = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    "<SOAP-ENV:Envelope\n"
    "    xmlns:SOAP-ENV=\"http://schemas.xmlsoap.org/soap/envelope/\"\n"
    "    xmlns:SOAP-ENC=\"http://schemas.xmlsoap.org/soap/encoding/\"\n"
    "    xmlns:xsi=\"http://www.w3.org/1999/XMLSchema-instance\"\n"
    "    xmlns:xsd=\"http://www.w3.org/1999/XMLSchema\"\n"
    "    xmlns:ns1=\"urn:AC\">\n"
    "  <SOAP-ENV:Body>\n"
    "    <ns1:executeCommand>\n"
    "      <command>{command}</command>\n"
    "    </ns1:executeCommand>\n"
    "  </SOAP-ENV:Body>\n"
    "</SOAP-ENV:Envelope>"
)
DEFAULT_INFO_COMMAND = "server info"
DEFAULT_ONLINE_COMMAND = "account onlinelist"
DEFAULT_BANNER_FILENAME = "wow-status-banner.png"
DEFAULT_BANNER_PATH = Path(__file__).parent / "assets" / DEFAULT_BANNER_FILENAME


class AzerothCore(commands.Cog):
    """Interact with an AzerothCore server through SOAP console commands."""

    def __init__(self, bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.config = Config.get_conf(self, identifier=4528967103, force_registration=True)
        self.config.register_global(
            server_name=None,
            realmlist=None,
            info_description=None,
            use_soap=True,
            soap_url=None,
            soap_user=None,
            soap_pass=None,
            soap_envelope_template=DEFAULT_SOAP_ENVELOPE_TEMPLATE,
            soap_create_command_template="account create {username} {password}",
            soap_info_command_template=DEFAULT_INFO_COMMAND,
            soap_online_command_template=DEFAULT_ONLINE_COMMAND,
            request_timeout=20,
        )
        self.config.register_guild(allowed_roles=[])

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def cog_unload(self):
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    def _render_template(self, value: Any, **replacements: Any) -> Any:
        if isinstance(value, str):
            return value.format(**replacements)
        if isinstance(value, list):
            return [self._render_template(item, **replacements) for item in value]
        if isinstance(value, dict):
            return {key: self._render_template(item, **replacements) for key, item in value.items()}
        return value

    @staticmethod
    def _redact_url(value: Optional[str]) -> str:
        if not value or value == LEGACY_PLACEHOLDER_SOAP_URL:
            return "Not set"

        try:
            parsed = urlsplit(value)
        except ValueError:
            return "Invalid URL"

        if "@" not in parsed.netloc:
            return value

        host = parsed.hostname or ""
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port:
            host = f"{host}:{port}"
        return urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment))

    @staticmethod
    def _normalize_soap_url(value: str) -> str:
        value = value.strip()
        if "://" not in value:
            value = f"http://{value}"

        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("SOAP URL must be an IP/hostname with a port, such as `192.168.1.1:7878`.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("SOAP URL port must be a number.") from exc
        if port is None:
            raise ValueError("SOAP URL must include a port, such as `192.168.1.1:7878`.")

        path = parsed.path or "/"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @staticmethod
    def _url_contains_credentials(value: str) -> bool:
        try:
            return "@" in urlsplit(value).netloc
        except ValueError:
            return False

    @staticmethod
    def _strip_url_credentials(value: str) -> str:
        parsed = urlsplit(value)
        if "@" not in parsed.netloc:
            return value

        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port:
            host = f"{host}:{port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))

    @classmethod
    def _redact_error(cls, error: Exception, *urls: Optional[str]) -> str:
        message = str(error)
        for url in urls:
            if url:
                message = message.replace(url, cls._redact_url(url))
        return message

    @staticmethod
    def _missing_soap_url_message(ctx: commands.Context) -> str:
        return (
            "SOAP URL has not been configured yet. Set it with "
            f"`{ctx.clean_prefix}ac set soap_url <ip:port>`."
        )

    @staticmethod
    async def _try_delete_invocation(ctx: commands.Context) -> None:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    async def _configured_soap_url(self) -> Optional[str]:
        soap_url = await self.config.soap_url()
        if not soap_url or soap_url == LEGACY_PLACEHOLDER_SOAP_URL:
            return None

        try:
            normalized_url = self._normalize_soap_url(soap_url)
        except ValueError:
            return soap_url

        if normalized_url != soap_url:
            await self.config.soap_url.set(normalized_url)
        return normalized_url

    async def _configured_envelope_template(self) -> str:
        envelope_template = await self.config.soap_envelope_template()
        if envelope_template == LEGACY_SOAP_ENVELOPE_TEMPLATE:
            await self.config.soap_envelope_template.set(DEFAULT_SOAP_ENVELOPE_TEMPLATE)
            return DEFAULT_SOAP_ENVELOPE_TEMPLATE
        return envelope_template

    async def _configured_online_command_template(self) -> str:
        command = await self.config.soap_online_command_template()
        if not command:
            return DEFAULT_ONLINE_COMMAND
        if command.strip().lower() == "players":
            await self.config.soap_online_command_template.set(DEFAULT_ONLINE_COMMAND)
            return DEFAULT_ONLINE_COMMAND
        return command

    @staticmethod
    def _extract_soap_result(text: str) -> str:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return text.strip()

        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "result":
                return "".join(element.itertext()).strip()

        return "".join(root.itertext()).strip()

    @staticmethod
    def _extract_soap_fault(text: str) -> str:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return text.strip()

        fault_fields = {}
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1].lower()
            if tag in {"faultcode", "faultstring", "detail"}:
                fault_fields[tag] = "".join(element.itertext()).strip()

        if fault_fields:
            parts = []
            if fault_fields.get("faultcode"):
                parts.append(fault_fields["faultcode"])
            if fault_fields.get("faultstring"):
                parts.append(fault_fields["faultstring"])
            if fault_fields.get("detail"):
                parts.append(fault_fields["detail"])
            return ": ".join(part for part in parts if part)

        return "".join(root.itertext()).strip()

    async def _soap_execute(self, command: str) -> Tuple[Optional[str], Optional[str]]:
        """Execute a SOAP envelope against the configured SOAP endpoint and return the inner text."""
        use_soap = await self.config.use_soap()
        if not use_soap:
            return None, "SOAP is not enabled."

        soap_url = await self._configured_soap_url()
        if not soap_url:
            return None, "SOAP URL has not been configured. Use `ac set soap_url` to set it."

        envelope_template = await self._configured_envelope_template()
        body = envelope_template.format(command=html.escape(command, quote=True))

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "Accept": "text/xml",
            "SOAPAction": "urn:AC#executeCommand",
        }
        soap_user = await self.config.soap_user()
        soap_pass = await self.config.soap_pass()
        auth = None
        request_url = soap_url
        if soap_user:
            auth = aiohttp.BasicAuth(soap_user, soap_pass or "")
            request_url = self._strip_url_credentials(soap_url)

        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

        try:
            timeout_seconds = await self.config.request_timeout()
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with self.session.post(request_url, data=body.encode(), headers=headers, timeout=timeout, auth=auth) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    detail = self._extract_soap_fault(text)
                    if not detail:
                        detail = text
                    return None, f"SOAP request failed with HTTP {resp.status}: {detail[:500]}"

                return self._extract_soap_result(text), None
        except asyncio.TimeoutError:
            return None, (
                f"The SOAP request timed out after {timeout_seconds} seconds while connecting to "
                f"{self._redact_url(soap_url)}. Check the IP/hostname, port, firewall or Docker port mapping, "
                "and whether AzerothCore SOAP is enabled."
            )
        except aiohttp.ClientConnectorError as exc:
            if getattr(exc, "os_error", None) and exc.os_error.errno == errno.ECONNREFUSED:
                return None, (
                    f"Could not connect to SOAP at {self._redact_url(soap_url)}. "
                    "The host answered, but nothing is listening on that IP/port. "
                    "Check the published port and whether SOAP is bound to that network interface."
                )
            detail = self._redact_error(exc, soap_url, request_url)
            return None, f"SOAP connection error: {detail}"
        except aiohttp.ServerDisconnectedError:
            return None, (
                f"SOAP disconnected while talking to {self._redact_url(soap_url)}. "
                "Something accepted the TCP connection and then closed it. "
                "Check that this is the AzerothCore SOAP port, not the world/auth port, and verify SOAP auth/access."
            )
        except aiohttp.ClientOSError as exc:
            if getattr(exc, "errno", None) == errno.ECONNRESET:
                return None, (
                    f"SOAP connection was reset by {self._redact_url(soap_url)}. "
                    "Something accepted the TCP connection and then closed it. "
                    "Check that this is the AzerothCore SOAP port and that SOAP is enabled with valid auth."
                )
            detail = self._redact_error(exc, soap_url, request_url)
            return None, f"SOAP connection error: {detail}"
        except Exception as exc:
            detail = self._redact_error(exc, soap_url, request_url)
            return None, f"SOAP request error: {detail}"

    @staticmethod
    def _parse_server_info(text: Optional[str]) -> Dict[str, Any]:
        """Parse common server info fields from console output into a dict."""
        out = (text or "").strip()
        result: Dict[str, Any] = {}
        if not out:
            return result

        import re
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]

        if lines and lines[0].lower().startswith("azerothcore"):
            result["build"] = lines[0]

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
        m = re.search(r"(?:Players\s*online|Connected\s*players):\s*(\d+)", out, flags=re.I)
        if not m:
            m = re.search(r"Online:\s*(\d+)", out, flags=re.I)
        if m:
            try:
                result["online"] = int(m.group(1))
            except Exception:
                result["online"] = m.group(1)

        m = re.search(r"Characters\s*in\s*world:\s*(\d+)", out, flags=re.I)
        if m:
            try:
                result["characters"] = int(m.group(1))
            except Exception:
                result["characters"] = m.group(1)

        m = re.search(r"Connection\s*peak:\s*(\d+)", out, flags=re.I)
        if m:
            try:
                result["peak"] = int(m.group(1))
            except Exception:
                result["peak"] = m.group(1)

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
            if lines:
                result.setdefault("description", lines[0])

        return result

    @staticmethod
    def _parse_players(text: Optional[str]) -> List[Dict[str, Any]]:
        """Parse player list console output into list of player dicts.

        Heuristics: look for lines with 'Name:' and 'Level:' or a simple
        character row that includes a level. Generic server-info lines are
        intentionally ignored.
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

            if ln.startswith("|-") or set(ln) <= {"|", "-", " "}:
                continue

            # Simple 'Name Level Zone' column format.
            parts = ln.split()
            if len(parts) >= 2 and parts[1].isdigit():
                player["name"] = parts[0]
                player["level"] = int(parts[1])
                if len(parts) > 2:
                    player["zone"] = " ".join(parts[2:])
                players.append(player)

        return players

    @staticmethod
    def _parse_online_accounts(text: Optional[str]) -> List[str]:
        """Parse AzerothCore account online-list output into account names."""
        out = (text or "").strip()
        if not out:
            return []

        import re

        accounts: List[str] = []
        seen = set()
        table_name_index: Optional[int] = None
        saw_account_list_header = False
        ignored_cells = {
            "id",
            "account",
            "account id",
            "accounts",
            "username",
            "user",
            "name",
            "online",
            "online accounts",
            "ip",
            "last ip",
            "gm",
            "security",
            "sec",
            "realm",
            "characters",
            "no",
            "none",
        }

        def add_account(candidate: str) -> None:
            candidate = candidate.strip().strip("`")
            if not candidate:
                return
            lowered = candidate.lower().strip(":")
            if lowered in ignored_cells:
                return
            if candidate.isdigit():
                return
            if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", candidate):
                return
            if not re.match(r"^[A-Za-z0-9_.@-]{2,32}$", candidate):
                return
            if lowered not in seen:
                seen.add(lowered)
                accounts.append(candidate)

        for line in out.splitlines():
            ln = line.strip()
            if not ln:
                continue

            lowered = ln.lower()
            if lowered.startswith("azerothcore"):
                continue
            if re.search(r"\bno\b.*\bonline\b.*\baccounts?\b", lowered):
                continue
            if re.search(r"\bonline\b.*\baccounts?\b", lowered):
                saw_account_list_header = True
            if lowered.startswith("soap-env:") or lowered.startswith("command "):
                continue
            if lowered.startswith("online accounts") and ":" not in ln:
                continue
            if set(ln) <= {"|", "-", "+", " "}:
                continue

            keyed = re.search(r"(?:Account|Username|User|Name):\s*([A-Za-z0-9_.@-]{2,32})", ln, flags=re.I)
            if keyed:
                add_account(keyed.group(1))
                continue
            if ":" in ln:
                continue

            if "|" in ln:
                cells = [cell.strip() for cell in ln.strip("|").split("|") if cell.strip()]
                lowered_cells = [cell.lower().strip(":") for cell in cells]
                if any(cell in ignored_cells for cell in lowered_cells):
                    for index, cell in enumerate(lowered_cells):
                        if cell in {"username", "user", "name", "account"}:
                            table_name_index = index
                            break
                    continue

                if table_name_index is not None and table_name_index < len(cells):
                    add_account(cells[table_name_index])
                    continue

                if len(cells) >= 2 and cells[0].isdigit():
                    add_account(cells[1])
                    continue

                for cell in cells:
                    previous_count = len(accounts)
                    add_account(cell)
                    if len(accounts) > previous_count:
                        break
                continue

            parts = ln.split()
            if len(parts) == 1 and saw_account_list_header:
                add_account(parts[0])
                continue

            if saw_account_list_header:
                for part in parts:
                    add_account(part)
                    if accounts and accounts[-1].lower() == part.lower():
                        break

        return accounts

    @staticmethod
    def _is_default_online_summary_command(command: str) -> bool:
        return command.strip().lower().lstrip(".") == DEFAULT_INFO_COMMAND

    @staticmethod
    def _is_default_online_list_command(command: str) -> bool:
        return command.strip().lower().lstrip(".") == DEFAULT_ONLINE_COMMAND

    @classmethod
    def _format_online_from_server_info(cls, text: Optional[str]) -> Optional[str]:
        info = cls._parse_server_info(text)
        if not info:
            return None

        if info.get("online") is None:
            return None

        summary = f"Connected players: {info['online']}"
        if info.get("characters") is not None:
            summary += f"\nCharacters in world: {info['characters']}"
        if info.get("peak") is not None:
            summary += f"\nConnection peak: {info['peak']}"
        return summary

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

    def _server_banner_file(self) -> Optional[discord.File]:
        if DEFAULT_BANNER_PATH.exists():
            return discord.File(str(DEFAULT_BANNER_PATH), filename=DEFAULT_BANNER_FILENAME)
        return None

    async def _send_embed(self, ctx: commands.Context, embed: discord.Embed, *, with_banner: bool = False) -> None:
        file = self._server_banner_file() if with_banner else None
        if file:
            await ctx.send(embed=embed, file=file)
        else:
            await ctx.send(embed=embed)

    async def _send_embed_or_text(
        self,
        ctx: commands.Context,
        embed: discord.Embed,
        text: str,
        *,
        with_banner: bool = False,
    ) -> None:
        if self._can_embed(ctx):
            await self._send_embed(ctx, embed, with_banner=with_banner)
        else:
            await ctx.send(text)

    async def _server_display_name(self) -> str:
        return await self.config.server_name() or "World of Warcraft"

    async def _decorate_server_embed(self, embed: discord.Embed, *, with_banner: bool = False) -> None:
        configured_description = await self.config.info_description()
        configured_realmlist = await self.config.realmlist()
        if configured_description:
            embed.description = str(configured_description)[:1900]
        if configured_realmlist:
            embed.add_field(name="Realmlist", value=str(configured_realmlist), inline=False)
        if with_banner and DEFAULT_BANNER_PATH.exists():
            embed.set_image(url=f"attachment://{DEFAULT_BANNER_FILENAME}")

    @commands.group(name="ac", aliases=("azerothcore",), invoke_without_command=True)
    async def ac(self, ctx: commands.Context):
        """AzerothCore server commands."""

        await ctx.send_help()

    @ac.command(name="info")
    async def ac_info(self, ctx: commands.Context):
        """Show general server information."""
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

        cmd_template = await self.config.soap_info_command_template()
        cmd = self._render_template(cmd_template)
        out, error = await self._soap_execute(cmd)
        if error:
            return await ctx.send(error)

        info = self._parse_server_info(out)

        title = info.get("name") or await self._server_display_name()
        embed = discord.Embed(title=title, color=await ctx.embed_color())
        configured_realmlist = await self.config.realmlist()
        if configured_realmlist:
            embed.add_field(name="Realmlist", value=str(configured_realmlist), inline=False)

        if info.get("online") is not None and info.get("max_players") is not None:
            embed.add_field(name="Population", value=f"{info.get('online')}/{info.get('max_players')}", inline=True)
        elif info.get("online") is not None:
            embed.add_field(name="Online", value=str(info.get("online")), inline=True)
        if info.get("characters") is not None:
            embed.add_field(name="Characters", value=str(info.get("characters")), inline=True)
        if info.get("peak") is not None:
            embed.add_field(name="Peak", value=str(info.get("peak")), inline=True)

        if info.get("version"):
            embed.add_field(name="Version", value=str(info.get("version")), inline=True)
        elif info.get("build"):
            embed.add_field(name="Build", value=str(info.get("build"))[:1024], inline=False)
        if info.get("expansion"):
            embed.add_field(name="Expansion", value=str(info.get("expansion")), inline=True)
        if info.get("uptime"):
            embed.add_field(name="Uptime", value=str(info.get("uptime")), inline=True)

        configured_description = await self.config.info_description()
        description = configured_description or info.get("description") or "Server information fetched successfully."
        embed.description = str(description)[:1900]

        fallback = [f"{title}"]
        if configured_realmlist:
            fallback.append(f"Realmlist: {configured_realmlist}")
        if info.get("online") is not None and info.get("max_players") is not None:
            fallback.append(f"Population: {info.get('online')}/{info.get('max_players')}")
        elif info.get("online") is not None:
            fallback.append(f"Online: {info.get('online')}")
        if info.get("characters") is not None:
            fallback.append(f"Characters: {info.get('characters')}")
        if info.get("peak") is not None:
            fallback.append(f"Peak: {info.get('peak')}")
        if info.get("version"):
            fallback.append(f"Version: {info.get('version')}")
        elif info.get("build"):
            fallback.append(f"Build: {info.get('build')}")
        if info.get("expansion"):
            fallback.append(f"Expansion: {info.get('expansion')}")
        if info.get("uptime"):
            fallback.append(f"Uptime: {info.get('uptime')}")

        await self._send_embed_or_text(ctx, embed, "\n".join(fallback))

    @ac.command(name="online")
    async def ac_online(self, ctx: commands.Context):
        """Show the characters currently online."""
        server_name = await self._server_display_name()
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

        cmd_template = await self._configured_online_command_template()
        cmd = self._render_template(cmd_template)
        fallback_notice = None

        if self._is_default_online_summary_command(cmd):
            out, error = await self._soap_execute(DEFAULT_ONLINE_COMMAND)
            used_command = DEFAULT_ONLINE_COMMAND
            if error:
                fallback_notice = (
                    "Could not get the online account list, so showing server counts instead.\n"
                    f"{error}"
                )
                out, error = await self._soap_execute(cmd)
                used_command = cmd
        else:
            out, error = await self._soap_execute(cmd)
            used_command = cmd

        if error:
            return await ctx.send(error)

        accounts = [] if self._is_default_online_summary_command(used_command) else self._parse_online_accounts(out)
        if accounts:
            header = f"Currently online: {len(accounts)} account{'s' if len(accounts) != 1 else ''}"
            if fallback_notice:
                header = f"{header}\n\n{fallback_notice}"
            embed = discord.Embed(title=server_name, color=await ctx.embed_color())
            await self._decorate_server_embed(embed, with_banner=True)

            if self._can_embed(ctx):
                pages = list(pagify("\n".join(accounts), delims=["\n"], page_length=900))
                if len(pages) == 1:
                    embed.add_field(name="Online Players", value=f"{header}\n{pages[0]}", inline=False)
                    await self._send_embed(ctx, embed, with_banner=True)
                    return

                embed.add_field(name="Online Players", value=header, inline=False)
                await self._send_embed(ctx, embed, with_banner=True)
                for page in pages:
                    await ctx.send(page)
                return

            await ctx.send(f"{server_name}\nOnline Players\n{header}\n" + "\n".join(accounts))
            return

        summary = self._format_online_from_server_info(out)
        if summary:
            if fallback_notice:
                summary = f"{summary}\n\n{fallback_notice}"
            embed = discord.Embed(title=server_name, color=await ctx.embed_color())
            await self._decorate_server_embed(embed, with_banner=True)
            embed.add_field(name="Online Players", value=summary, inline=False)
            await self._send_embed_or_text(ctx, embed, f"{server_name}\nOnline Players\n{summary}", with_banner=True)
            return

        players = self._parse_players(out)
        if not players:
            if self._is_default_online_list_command(cmd):
                summary_out, summary_error = await self._soap_execute(DEFAULT_INFO_COMMAND)
                summary = self._format_online_from_server_info(summary_out)
                if summary and not summary_error:
                    message = "No account names were returned, so showing server counts instead.\n\n" f"{summary}"
                    embed = discord.Embed(title=server_name, color=await ctx.embed_color())
                    await self._decorate_server_embed(embed, with_banner=True)
                    embed.add_field(name="Online Players", value=message, inline=False)
                    await self._send_embed_or_text(
                        ctx,
                        embed,
                        f"{server_name}\nOnline Players\n{message}",
                        with_banner=True,
                    )
                    return
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
        embed = discord.Embed(title=server_name, color=await ctx.embed_color())
        await self._decorate_server_embed(embed, with_banner=True)

        if self._can_embed(ctx):
            pages = list(pagify("\n".join(formatted), delims=["\n"], page_length=900))
            if len(pages) == 1:
                embed.add_field(name="Online Players", value=f"{header}\n{pages[0]}", inline=False)
                await self._send_embed(ctx, embed, with_banner=True)
                return

            embed.add_field(name="Online Players", value=header, inline=False)
            await self._send_embed(ctx, embed, with_banner=True)
            for page in pages:
                await ctx.send(page)
            return

        await ctx.send(f"{server_name}\nOnline Players\n{header}\n" + "\n".join(formatted))

    @ac.command(name="check")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ac_check(self, ctx: commands.Context):
        """Check whether the configured SOAP host and port are reachable."""

        soap_url = await self._configured_soap_url()
        if not soap_url:
            return await ctx.send(self._missing_soap_url_message(ctx))

        try:
            parsed = urlsplit(soap_url)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            return await ctx.send(f"SOAP URL is invalid: {exc}")

        if not host or port is None:
            return await ctx.send("SOAP URL must include a host and port.")

        timeout_seconds = min(await self.config.request_timeout(), 10)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout_seconds,
            )
            writer.close()
            await writer.wait_closed()
        except asyncio.TimeoutError:
            return await ctx.send(
                f"TCP check timed out after {timeout_seconds} seconds for {self._redact_url(soap_url)}."
            )
        except ConnectionRefusedError:
            return await ctx.send(
                f"TCP check failed for {self._redact_url(soap_url)}: host answered, but nothing is listening "
                "on that IP/port."
            )
        except ConnectionResetError:
            return await ctx.send(
                f"TCP check reached {self._redact_url(soap_url)}, but the connection was reset."
            )
        except OSError as exc:
            return await ctx.send(f"TCP check failed for {self._redact_url(soap_url)}: {exc}")

        await ctx.send(
            f"TCP check passed for {self._redact_url(soap_url)}. "
            "This only confirms the port is open; `ac info` or `ac online` still verifies the SOAP request."
        )

    @ac.command(name="raw")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ac_raw(self, ctx: commands.Context, *, command: str):
        """Run a raw SOAP console command and show the response."""

        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

        out, error = await self._soap_execute(command)
        if error:
            return await ctx.send(error)

        response = (out or "").strip() or "Command returned no output."
        for page in pagify(response, delims=["\n"], page_length=1800):
            await ctx.send(box(page, lang="text"))

    @ac.command(name="createuser", aliases=("createaccount",))
    @commands.guild_only()
    async def ac_createuser(self, ctx: commands.Context, username: str, email: Optional[str] = None):
        """Create a new game account through the configured API bridge."""

        if not await self._can_create_accounts(ctx):
            return await ctx.send("You do not have permission to create accounts.")
        # Use SOAP to create account
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

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
            hint = "SOAP access denied - check SOAP credentials and server permissions."
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
            "**AzerothCore Module Settings (SOAP-only)**\n\n"
            "**Basics**\n"
            "- `ac set soap_url <ip:port>`\n"
            "- `ac set soap_auth <user> <pass>`\n"
            "- `ac set view`\n"
            "- `ac check`\n\n"
            "**Display**\n"
            "- `ac set servername <name>`\n"
            "- `ac set realmlist <text>`\n"
            "- `ac set infodescription <text>`\n\n"
            "**Account Creation Roles**\n"
            "- `ac set accountcreationrole <role...>`\n"
            "- `ac set roleremove <role...>`\n"
            "- `ac set rolelist`\n\n"
            "**Advanced**\n"
            "- `ac set timeout <seconds>`\n"
            "- `ac set onlinecommand <command>`\n"
            "- `ac set infocommand <command>`\n"
            "- `ac set createcommand <command>`\n"
            "- `ac raw <command>`\n\n"
            "Example: `ac set soap_url 192.168.1.1:7878`"
        )
        await ctx.send(message)

    @acset.command(name="baseurl", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_baseurl(self, ctx: commands.Context, base_url: str):
        """Legacy bridge setting. Use soap_url instead."""

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

    @acset.command(name="token", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_token(self, ctx: commands.Context, token: Optional[str] = None):
        """Legacy bridge setting. Use soap_auth instead."""

        await ctx.send("Token is not used in SOAP-only mode. Use `ac set soap_auth` to configure SOAP credentials.")

    @acset.command(name="statuspath", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_statuspath(self, ctx: commands.Context, path: str):
        """Legacy bridge setting."""

        await ctx.send("Status path is not used in SOAP-only mode.")

    @acset.command(name="onlinepath", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_onlinepath(self, ctx: commands.Context, path: str):
        """Legacy bridge setting."""

        await ctx.send("Online path is not used in SOAP-only mode.")

    @acset.command(name="createpath", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createpath(self, ctx: commands.Context, path: str):
        """Legacy bridge setting."""

        await ctx.send("Create path is not used in SOAP-only mode.")

    @acset.command(name="createmethod", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createmethod(self, ctx: commands.Context, method: str):
        """Legacy bridge setting."""

        await ctx.send("Create method is not used in SOAP-only mode.")

    @acset.command(name="createbody", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createbody(self, ctx: commands.Context, *, create_body: Optional[str] = None):
        """Legacy bridge setting."""

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

    @acset.command(name="soap_url", aliases=("soapurl",))
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_soap_url(self, ctx: commands.Context, soap_url: Optional[str] = None):
        """Set or clear the SOAP endpoint URL."""

        if not soap_url:
            await self.config.soap_url.clear()
            return await ctx.send("SOAP URL cleared.")

        try:
            normalized_url = self._normalize_soap_url(soap_url)
        except ValueError as exc:
            return await ctx.send(str(exc))

        await self.config.soap_url.set(normalized_url)
        if self._url_contains_credentials(normalized_url):
            await self._try_delete_invocation(ctx)
        await ctx.send(f"SOAP URL set to {self._redact_url(normalized_url)}")

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
        await self._try_delete_invocation(ctx)
        await ctx.send("SOAP credentials updated.")

    @acset.command(name="timeout")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_timeout(self, ctx: commands.Context, seconds: Optional[int] = None):
        """Set or show the SOAP request timeout in seconds."""

        if seconds is None:
            timeout = await self.config.request_timeout()
            return await ctx.send(f"SOAP request timeout is currently {timeout} seconds.")

        if seconds < 3 or seconds > 120:
            return await ctx.send("Timeout must be between 3 and 120 seconds.")

        await self.config.request_timeout.set(seconds)
        await ctx.send(f"SOAP request timeout set to {seconds} seconds.")

    @acset.command(name="onlinecommand")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_onlinecommand(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Set, show, or reset the SOAP command used by ac online."""

        if command is None:
            current = await self.config.soap_online_command_template()
            return await ctx.send(f"SOAP online command is currently `{current}`.")
        if command.lower() == "reset":
            await self.config.soap_online_command_template.clear()
            return await ctx.send(f"SOAP online command reset to `{DEFAULT_ONLINE_COMMAND}`.")

        await self.config.soap_online_command_template.set(command)
        await ctx.send(f"SOAP online command set to `{command}`.")

    @acset.command(name="infocommand")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_infocommand(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Set, show, or reset the SOAP command used by ac info."""

        if command is None:
            current = await self.config.soap_info_command_template()
            return await ctx.send(f"SOAP info command is currently `{current}`.")
        if command.lower() == "reset":
            await self.config.soap_info_command_template.clear()
            return await ctx.send("SOAP info command reset to `server info`.")

        await self.config.soap_info_command_template.set(command)
        await ctx.send(f"SOAP info command set to `{command}`.")

    @acset.command(name="createcommand")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_createcommand(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Set, show, or reset the SOAP command template used by ac createuser."""

        if command is None:
            current = await self.config.soap_create_command_template()
            return await ctx.send(f"SOAP create command is currently `{current}`.")
        if command.lower() == "reset":
            await self.config.soap_create_command_template.clear()
            return await ctx.send("SOAP create command reset to `account create {username} {password}`.")
        if "{username}" not in command or "{password}" not in command:
            return await ctx.send("Create command must include `{username}` and `{password}` placeholders.")

        await self.config.soap_create_command_template.set(command)
        await ctx.send("SOAP create command updated.")

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
        soap_envelope = await self._configured_envelope_template()
        soap_create = await self.config.soap_create_command_template()
        soap_online = await self.config.soap_online_command_template()
        request_timeout = await self.config.request_timeout()
        allowed_roles = await self.config.guild(ctx.guild).allowed_roles()

        embed = discord.Embed(title="AzerothCore Configuration (SOAP)", color=await ctx.embed_color())
        embed.add_field(name="SOAP URL", value=self._redact_url(soap_url), inline=False)
        embed.add_field(name="SOAP Auth", value=("Set" if soap_user else "Not set"), inline=True)
        embed.add_field(name="Timeout", value=f"{request_timeout}s", inline=True)
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
