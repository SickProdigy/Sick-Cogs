import asyncio
import errno
import html
import re
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
DEFAULT_PLAYERBOT_ACCOUNT_PREFIXES = ["RNDBOT"]
ONLINE_PROBE_COMMANDS = (
    DEFAULT_ONLINE_COMMAND,
    f".{DEFAULT_ONLINE_COMMAND}",
    DEFAULT_INFO_COMMAND,
)
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
            playerbot_account_prefixes=DEFAULT_PLAYERBOT_ACCOUNT_PREFIXES,
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
            f"`{ctx.clean_prefix}azerothcore set soap_url <ip:port>`."
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
            return None, "SOAP URL has not been configured. Use `azerothcore set soap_url` to set it."

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

            bracket_cells = re.findall(r"\[([^\]]+)\]", ln)
            if bracket_cells:
                cells = [cell.strip() for cell in bracket_cells]
                lowered_cells = [cell.lower().strip(":") for cell in cells]
                if any(cell in {"account", "character", "ip", "map", "zone", "exp", "gmlev"} for cell in lowered_cells):
                    continue
                if len(cells) >= 7:
                    players.append(
                        {
                            "raw": ln,
                            "account": cells[0],
                            "name": cells[1],
                            "ip": cells[2],
                            "map": cells[3],
                            "zone": cells[4],
                            "expansion": cells[5],
                            "gm_level": cells[6],
                        }
                    )
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

            bracket_cells = re.findall(r"\[([^\]]+)\]", ln)
            if bracket_cells:
                cells = [cell.strip() for cell in bracket_cells]
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

                if cells:
                    add_account(cells[0])
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

    @classmethod
    def _online_response_hint(cls, text: Optional[str]) -> Optional[str]:
        lowered = (text or "").lower()
        if "low security" in lowered or "not have permission" in lowered:
            return (
                f"`{DEFAULT_ONLINE_COMMAND}` is responding, but the SOAP account does not have enough "
                "AzerothCore command security/RBAC permission to run it."
            )
        if "command" in lowered and ("not exist" in lowered or "unknown" in lowered):
            return "The configured online command was not recognized by AzerothCore."
        return None

    @staticmethod
    def _is_empty_online_response(text: Optional[str]) -> bool:
        lowered = (text or "").strip().lower()
        return bool(re.search(r"\bno\b.*\bonline\b.*\b(players?|accounts?|characters?)\b", lowered))

    def _summarize_online_probe_result(
        self, command: str, output: Optional[str], error: Optional[str]
    ) -> str:
        text = (output or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if error:
            status = f"SOAP error: {error}"
        else:
            accounts = [] if self._is_default_online_summary_command(command) else self._parse_online_accounts(text)
            players = self._parse_players(text)
            summary = self._format_online_from_server_info(text)
            hint = self._online_response_hint(text)

            if accounts:
                status = f"Parsed {len(accounts)} account name{'s' if len(accounts) != 1 else ''}."
            elif players:
                status = f"Parsed {len(players)} player row{'s' if len(players) != 1 else ''}."
            elif self._is_empty_online_response(text):
                status = "Command worked and reported no online players."
            elif summary:
                status = "Returned server counts, but no account or character names."
            elif hint:
                status = hint
            else:
                status = "No account names, player rows, or server counts were recognized."

        snippet = "\n".join(lines[:8]) if lines else "No output."
        if len(snippet) > 900:
            snippet = f"{snippet[:897]}..."
        return f"`{command}`\n{status}\n{box(snippet, lang='text')}"

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

    @staticmethod
    def _clean_role_input(role_input: str) -> str:
        return role_input.strip().strip("*`_~ ,")

    @staticmethod
    def _inline_role_input(role_input: str) -> str:
        return f"`{role_input.replace('`', '').strip()}`"

    def _resolve_role_input(self, ctx: commands.Context, role_input: str) -> Optional[discord.Role]:
        if ctx.guild is None:
            return None

        cleaned = self._clean_role_input(role_input)
        if not cleaned:
            return None

        role_id: Optional[int] = None
        mention_match = re.fullmatch(r"<@&(\d+)>", cleaned)
        if mention_match:
            role_id = int(mention_match.group(1))
        elif cleaned.isdigit():
            role_id = int(cleaned)

        if role_id is not None:
            return ctx.guild.get_role(role_id)

        role_name = cleaned.lstrip("@").casefold()
        for role in ctx.guild.roles:
            if role.name.casefold() == role_name:
                return role

        return None

    def _resolve_role_inputs(
        self, ctx: commands.Context, role_inputs: Tuple[str, ...]
    ) -> Tuple[List[discord.Role], List[str]]:
        roles: List[discord.Role] = []
        missing: List[str] = []
        seen = set()

        for role_input in role_inputs:
            role = self._resolve_role_input(ctx, role_input)
            if role is None:
                missing.append(role_input)
                continue
            if role.id in seen:
                continue
            seen.add(role.id)
            roles.append(role)

        return roles, missing

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

    async def _configured_playerbot_prefixes(self) -> List[str]:
        prefixes = await self.config.playerbot_account_prefixes()
        if not isinstance(prefixes, list):
            await self.config.playerbot_account_prefixes.set(DEFAULT_PLAYERBOT_ACCOUNT_PREFIXES)
            return list(DEFAULT_PLAYERBOT_ACCOUNT_PREFIXES)

        cleaned = []
        for prefix in prefixes:
            if not isinstance(prefix, str):
                continue
            prefix = prefix.strip()
            if prefix and prefix.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(prefix)
        return cleaned

    @staticmethod
    def _is_playerbot_account(account: Optional[str], prefixes: List[str]) -> bool:
        if not account:
            return False
        account_name = account.casefold()
        return any(account_name.startswith(prefix.casefold()) for prefix in prefixes if prefix)

    def _is_playerbot_record(self, record: Dict[str, Any], prefixes: List[str]) -> bool:
        account = record.get("account") or record.get("name")
        return self._is_playerbot_account(str(account), prefixes)

    @staticmethod
    def _format_online_record(record: Dict[str, Any]) -> str:
        name = str(record.get("name") or record.get("account") or record.get("raw") or "").strip()
        account = str(record.get("account") or "").strip()
        if account and name and account.casefold() != name.casefold():
            return f"{name} ({account})"
        return name or account or str(record.get("raw") or "").strip()

    @staticmethod
    def _format_character_name(record: Dict[str, Any]) -> str:
        return str(record.get("name") or record.get("account") or record.get("raw") or "").strip()

    async def _send_online_entries(
        self,
        ctx: commands.Context,
        *,
        server_name: str,
        field_name: str,
        header: str,
        entries: List[str],
        footer: Optional[str] = None,
        title_name: Optional[str] = None,
    ) -> None:
        embed_title = title_name or field_name
        embed = discord.Embed(title=f"{server_name} {embed_title}", color=await ctx.embed_color())
        await self._decorate_server_embed(embed, with_banner=True)

        if self._can_embed(ctx):
            pages = list(pagify("\n".join(entries), delims=["\n"], page_length=900))
            if len(pages) == 1:
                value = f"{header}\n{pages[0]}" if header else pages[0]
                if footer:
                    value = f"{value}\n\n{footer}"
                embed.add_field(name=field_name, value=value, inline=False)
                await self._send_embed(ctx, embed, with_banner=True)
                return

            field_header = header or "See below."
            if footer:
                field_header = f"{field_header}\n\n{footer}"
            embed.add_field(name=field_name, value=field_header, inline=False)
            await self._send_embed(ctx, embed, with_banner=True)
            for page in pages:
                await ctx.send(page)
            return

        lines = [server_name, field_name]
        if header:
            lines.append(header)
        lines.extend(entries)
        if footer:
            lines.extend(["", footer])
        await ctx.send("\n".join(lines))

    async def _fetch_online_output(self) -> Tuple[str, Optional[str], str, Optional[str]]:
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

        return out or "", error, used_command, fallback_notice

    async def _resolve_member_input(
        self, ctx: commands.Context, member_input: str
    ) -> Optional[discord.Member]:
        try:
            return await commands.MemberConverter().convert(ctx, member_input)
        except commands.BadArgument:
            return None

    async def _parse_account_create_details(
        self, ctx: commands.Context, details: Tuple[str, ...]
    ) -> Tuple[Optional[str], Optional[discord.Member], Optional[str]]:
        if not details:
            return None, ctx.author, None

        remaining = list(details)
        target = await self._resolve_member_input(ctx, remaining[-1])
        if target is not None:
            remaining.pop()
        else:
            target = ctx.author

        if len(remaining) > 1:
            return (
                None,
                None,
                "Usage: `azerothcore createuser <username> [email] [@member]` or `azerothcore accountcreate <username> [email] [@member]`.",
            )

        email = remaining[0] if remaining else None
        return email, target, None

    async def _send_account_welcome_dm(
        self,
        recipient: discord.Member,
        *,
        username: str,
        password: str,
        email: Optional[str],
    ) -> None:
        server_name = await self._server_display_name()
        realmlist = await self.config.realmlist()

        embed = discord.Embed(
            title=f"Welcome to {server_name}",
            description="Your game account is ready. Keep these login details private.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Username", value=f"`{username}`", inline=True)
        embed.add_field(name="Password", value=f"`{password}`", inline=True)
        if email:
            embed.add_field(name="Email", value=f"`{email}`", inline=False)
        if realmlist:
            embed.add_field(name="Realmlist", value=f"`{realmlist}`", inline=False)
        embed.add_field(
            name="Next Steps",
            value="Log in with the username and password above, then change your password if the server supports it.",
            inline=False,
        )

        file = self._server_banner_file()
        if file:
            embed.set_image(url=f"attachment://{DEFAULT_BANNER_FILENAME}")
            await recipient.send(embed=embed, file=file)
        else:
            await recipient.send(embed=embed)

    async def _decorate_server_embed(self, embed: discord.Embed, *, with_banner: bool = False) -> None:
        configured_realmlist = await self.config.realmlist()
        if configured_realmlist:
            embed.set_footer(text=f"Realmlist: {configured_realmlist}")
        if with_banner and DEFAULT_BANNER_PATH.exists():
            embed.set_image(url=f"attachment://{DEFAULT_BANNER_FILENAME}")

    @commands.group(name="azerothcore", aliases=("ac", "wow"), invoke_without_command=True)
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
        """Show the non-playerbot characters currently online."""
        server_name = await self._server_display_name()
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

        out, error, used_command, fallback_notice = await self._fetch_online_output()
        if error:
            return await ctx.send(error)

        prefixes = await self._configured_playerbot_prefixes()
        players = self._parse_players(out)
        if players:
            visible_players = [player for player in players if not self._is_playerbot_record(player, prefixes)]
            playerbot_count = len(players) - len(visible_players)
            bot_footer = f"Playerbots Online: {playerbot_count}" if playerbot_count else None
            if visible_players:
                entries = [self._format_character_name(player) for player in visible_players]
                return await self._send_online_entries(
                    ctx,
                    server_name=server_name,
                    field_name=f"Online Players: {len(entries)}",
                    header="",
                    entries=entries,
                    footer=bot_footer,
                    title_name="Online List",
                )

            message = "No real players are online."
            if playerbot_count:
                message += f"\n\nPlayerbots Online: {playerbot_count}"
            return await self._send_embed_or_text(
                ctx,
                discord.Embed(title=f"{server_name} Online List", color=await ctx.embed_color()).add_field(
                    name="Online Players: 0",
                    value=message,
                    inline=False,
                ),
                f"{server_name}\nOnline Players\n{message}",
                with_banner=True,
            )

        accounts = [] if self._is_default_online_summary_command(used_command) else self._parse_online_accounts(out)
        if accounts:
            visible_accounts = [account for account in accounts if not self._is_playerbot_account(account, prefixes)]
            playerbot_count = len(accounts) - len(visible_accounts)
            bot_footer = f"Playerbots Online: {playerbot_count}" if playerbot_count else None
            if visible_accounts:
                header = ""
                if fallback_notice:
                    header = fallback_notice
                return await self._send_online_entries(
                    ctx,
                    server_name=server_name,
                    field_name=f"Online Players: {len(visible_accounts)}",
                    header=header,
                    entries=visible_accounts,
                    footer=bot_footer,
                    title_name="Online List",
                )

            message = "No real player accounts are online."
            if playerbot_count:
                message += f"\n\nPlayerbots Online: {playerbot_count}"
            return await self._send_embed_or_text(
                ctx,
                discord.Embed(title=f"{server_name} Online List", color=await ctx.embed_color()).add_field(
                    name="Online Players: 0",
                    value=message,
                    inline=False,
                ),
                f"{server_name}\nOnline Players\n{message}",
                with_banner=True,
            )

        summary = self._format_online_from_server_info(out)
        if summary:
            if fallback_notice:
                summary = f"{summary}\n\n{fallback_notice}"
            embed = discord.Embed(title=f"{server_name} Online List", color=await ctx.embed_color())
            await self._decorate_server_embed(embed, with_banner=True)
            embed.add_field(name="Online Players", value=summary, inline=False)
            await self._send_embed_or_text(ctx, embed, f"{server_name}\nOnline Players\n{summary}", with_banner=True)
            return

        if self._is_empty_online_response(out):
            embed = discord.Embed(title=f"{server_name} Online List", color=await ctx.embed_color())
            await self._decorate_server_embed(embed, with_banner=True)
            embed.add_field(name="Online Players: 0", value="No online players.", inline=False)
            await self._send_embed_or_text(
                ctx,
                embed,
                f"{server_name}\nOnline Players\nNo online players.",
                with_banner=True,
            )
            return

        if self._is_default_online_list_command(used_command):
            summary_out, summary_error = await self._soap_execute(DEFAULT_INFO_COMMAND)
            summary = self._format_online_from_server_info(summary_out)
            if summary and not summary_error:
                hint = self._online_response_hint(out)
                message = "No account names were returned, so showing server counts instead."
                if hint:
                    message += f"\n\n{hint}"
                message += f"\n\n{summary}"
                embed = discord.Embed(title=f"{server_name} Online List", color=await ctx.embed_color())
                await self._decorate_server_embed(embed, with_banner=True)
                embed.add_field(name="Online Players", value=message, inline=False)
                await self._send_embed_or_text(
                    ctx,
                    embed,
                    f"{server_name}\nOnline Players\n{message}",
                    with_banner=True,
                )
                return

        await ctx.send("No online player information returned.")

    @ac.command(name="playerbots")
    async def ac_playerbots(self, ctx: commands.Context):
        """Show online playerbot characters filtered by configured account prefixes."""
        server_name = await self._server_display_name()
        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

        out, error, used_command, fallback_notice = await self._fetch_online_output()
        if error:
            return await ctx.send(error)

        prefixes = await self._configured_playerbot_prefixes()
        if not prefixes:
            return await ctx.send("Playerbot account prefixes are disabled. Configure them with `azerothcore set playerbotprefix <prefix...>`.")

        players = self._parse_players(out)
        if players:
            playerbots = [player for player in players if self._is_playerbot_record(player, prefixes)]
            if playerbots:
                entries = [self._format_online_record(player) for player in playerbots]
                header = f"Currently online: {len(entries)} playerbot{'s' if len(entries) != 1 else ''}"
                return await self._send_online_entries(
                    ctx,
                    server_name=server_name,
                    field_name="Online Playerbots",
                    header=header,
                    entries=entries,
                )

            return await ctx.send("No configured playerbot accounts are online.")

        accounts = [] if self._is_default_online_summary_command(used_command) else self._parse_online_accounts(out)
        playerbot_accounts = [account for account in accounts if self._is_playerbot_account(account, prefixes)]
        if playerbot_accounts:
            header = f"Currently online: {len(playerbot_accounts)} playerbot account{'s' if len(playerbot_accounts) != 1 else ''}"
            if fallback_notice:
                header = f"{header}\n\n{fallback_notice}"
            return await self._send_online_entries(
                ctx,
                server_name=server_name,
                field_name="Online Playerbots",
                header=header,
                entries=playerbot_accounts,
            )

        if self._is_empty_online_response(out):
            return await ctx.send("No online playerbots.")

        await ctx.send("No configured playerbot accounts were found in the online output.")

    @ac.command(name="onlineprobe")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ac_onlineprobe(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Probe SOAP online-list commands and show parse/security results."""

        if not await self.config.use_soap():
            return await ctx.send("SOAP transport is not enabled. Configure SOAP settings to use this command.")
        if not await self._configured_soap_url():
            return await ctx.send(self._missing_soap_url_message(ctx))

        commands_to_probe = (command.strip(),) if command and command.strip() else ONLINE_PROBE_COMMANDS
        results = []
        async with ctx.typing():
            for probe_command in commands_to_probe:
                out, error = await self._soap_execute(probe_command)
                results.append(self._summarize_online_probe_result(probe_command, out, error))

        for page in pagify("\n\n".join(results), delims=["\n\n"], page_length=1800):
            await ctx.send(page)

    @ac.command(name="soapcheck", aliases=("check",))
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
            "This only confirms the port is open; `azerothcore info` or `azerothcore online` still verifies the SOAP request."
        )

    @ac.command(name="raw")
    @commands.guild_only()
    async def ac_raw(self, ctx: commands.Context, *, command: str):
        """Run a raw SOAP console command and show the response.

        Restricted to server owners, server admins/managers, and roles
        configured with `azerothcore set accountcreationrole`.
        """

        if not await self._can_create_accounts(ctx):
            return await ctx.send("You do not have permission to run raw AzerothCore commands.")
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

    @ac.command(name="createuser", aliases=("createaccount", "accountcreate"))
    @commands.guild_only()
    async def ac_createuser(self, ctx: commands.Context, username: str, *details: str):
        """Create a game account through SOAP and DM the credentials.

        Restricted to server owners, server admins/managers, and roles
        configured with `azerothcore set accountcreationrole`.

        If a Discord member is provided as the final argument, the bot DMs
        that member. Otherwise, it DMs the command author.
        """

        if not await self._can_create_accounts(ctx):
            return await ctx.send("You do not have permission to create accounts.")

        email, target, parse_error = await self._parse_account_create_details(ctx, details)
        if parse_error:
            return await ctx.send(parse_error)
        if target is None:
            return await ctx.send("I could not find that Discord member.")

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
        if ("created" in lower_out or "success" in lower_out) and "already" not in lower_out:
            try:
                await self._send_account_welcome_dm(target, username=username, password=password, email=email)
            except (discord.Forbidden, discord.HTTPException):
                if target.id == ctx.author.id:
                    await ctx.send(
                        "Account created, but I could not DM you the password. Please enable DMs and try again if needed."
                    )
                else:
                    await ctx.send(
                        f"Account created for {target.mention}, but I could not DM them the login details. "
                        "Ask them to enable DMs, then create a new password if needed."
                    )
            else:
                if target.id == ctx.author.id:
                    await ctx.send("Account created. I sent the login details to you in DMs.")
                else:
                    await ctx.send(f"Account created for {target.mention}. I sent the login details to them in DMs.")
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
            "- `azerothcore set soap_url <ip:port>`\n"
            "- `azerothcore set soap_auth <user> <pass>`\n"
            "- `azerothcore set view`\n"
            "- `azerothcore soapcheck`\n\n"
            "**Display**\n"
            "- `azerothcore set servername <name>`\n"
            "- `azerothcore set realmlist <text>`\n"
            "- `azerothcore set infodescription <text>`\n\n"
            "**Account Creation Roles**\n"
            "- `azerothcore set accountcreationrole <role...>`\n"
            "- `azerothcore set roleremove <role...>`\n"
            "- `azerothcore set rolelist`\n\n"
            "**Advanced**\n"
            "- `azerothcore set timeout <seconds>`\n"
            "- `azerothcore set onlinecommand <command>`\n"
            "- `azerothcore set playerbotprefix <prefix...>`\n"
            "- `azerothcore set infocommand <command>`\n"
            "- `azerothcore set createcommand <command>`\n"
            "- `azerothcore raw <command>`\n"
            "- `azerothcore playerbots`\n"
            "- `azerothcore onlineprobe [command]`\n\n"
            "Example: `azerothcore set soap_url 192.168.1.1:7878`"
        )
        await ctx.send(message)

    @acset.command(name="baseurl", hidden=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_baseurl(self, ctx: commands.Context, base_url: str):
        """Legacy bridge setting. Use soap_url instead."""

        await ctx.send("Base URL is managed by SOAP-only configuration. Use `azerothcore set soap_url`.")

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

        await ctx.send("Token is not used in SOAP-only mode. Use `azerothcore set soap_auth` to configure SOAP credentials.")

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
    async def acset_accountcreationrole(self, ctx: commands.Context, *role_inputs: str):
        """Allow roles to create accounts and run restricted account tools.

        Roles can be provided as mentions, IDs, or exact names.
        """

        if not role_inputs:
            return await ctx.send("You need to provide at least one role.")

        roles, missing = self._resolve_role_inputs(ctx, role_inputs)
        if missing:
            missing_roles = humanize_list([self._inline_role_input(role_input) for role_input in missing])
            return await ctx.send(
                f"I could not find {missing_roles}. Use a role mention, role ID, or exact role name."
            )

        if not roles:
            return await ctx.send("I could not find any matching roles.")

        async with self.config.guild(ctx.guild).allowed_roles() as allowed_roles:
            for role in roles:
                if role.id not in allowed_roles:
                    allowed_roles.append(role.id)

        await ctx.send(f"Allowed roles updated: {humanize_list([role.mention for role in roles])}")

    @acset.command(name="roleremove")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_roleremove(self, ctx: commands.Context, *role_inputs: str):
        """Remove roles from the account-creation allowlist."""

        if not role_inputs:
            return await ctx.send("You need to provide at least one role.")

        roles, missing = self._resolve_role_inputs(ctx, role_inputs)
        if missing:
            missing_roles = humanize_list([self._inline_role_input(role_input) for role_input in missing])
            return await ctx.send(
                f"I could not find {missing_roles}. Use a role mention, role ID, or exact role name."
            )

        if not roles:
            return await ctx.send("I could not find any matching roles.")

        removed: List[discord.Role] = []
        async with self.config.guild(ctx.guild).allowed_roles() as allowed_roles:
            for role in roles:
                if role.id in allowed_roles:
                    allowed_roles.remove(role.id)
                    removed.append(role)

        if removed:
            await ctx.send(f"Removed: {humanize_list([role.mention for role in removed])}")
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
            if not isinstance(role_id, int):
                continue
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
        """Set, show, or reset the SOAP command used by azerothcore online."""

        if command is None:
            current = await self.config.soap_online_command_template()
            return await ctx.send(f"SOAP online command is currently `{current}`.")
        if command.lower() == "reset":
            await self.config.soap_online_command_template.clear()
            return await ctx.send(f"SOAP online command reset to `{DEFAULT_ONLINE_COMMAND}`.")

        await self.config.soap_online_command_template.set(command)
        await ctx.send(f"SOAP online command set to `{command}`.")

    @acset.command(name="playerbotprefix", aliases=("playerbotprefixes",))
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_playerbotprefix(self, ctx: commands.Context, *prefixes: str):
        """Set account prefixes used to identify playerbots in online output."""

        if not prefixes:
            current = await self._configured_playerbot_prefixes()
            value = humanize_list([f"`{prefix}`" for prefix in current]) if current else "disabled"
            return await ctx.send(f"Playerbot account prefixes are currently {value}.")

        action = prefixes[0].casefold()
        if len(prefixes) == 1 and action == "reset":
            await self.config.playerbot_account_prefixes.set(DEFAULT_PLAYERBOT_ACCOUNT_PREFIXES)
            return await ctx.send(
                "Playerbot account prefixes reset to "
                f"{humanize_list([f'`{prefix}`' for prefix in DEFAULT_PLAYERBOT_ACCOUNT_PREFIXES])}."
            )
        if len(prefixes) == 1 and action in {"clear", "disable", "off", "none"}:
            await self.config.playerbot_account_prefixes.set([])
            return await ctx.send("Playerbot account prefix filtering disabled.")

        cleaned = []
        for prefix in prefixes:
            prefix = prefix.strip()
            if prefix and prefix.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(prefix)

        if not cleaned:
            return await ctx.send("You need to provide at least one non-empty prefix.")

        await self.config.playerbot_account_prefixes.set(cleaned)
        await ctx.send(f"Playerbot account prefixes set to {humanize_list([f'`{prefix}`' for prefix in cleaned])}.")

    @acset.command(name="infocommand")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def acset_infocommand(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Set, show, or reset the SOAP command used by azerothcore info."""

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
        """Set, show, or reset the SOAP command template used by azerothcore createuser."""

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
        playerbot_prefixes = await self._configured_playerbot_prefixes()
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
        embed.add_field(
            name="Playerbot Prefixes",
            value=humanize_list([f"`{prefix}`" for prefix in playerbot_prefixes]) if playerbot_prefixes else "Disabled",
            inline=False,
        )

        role_mentions = []
        for role_id in allowed_roles:
            if not isinstance(role_id, int):
                continue
            role = ctx.guild.get_role(role_id)
            if role is not None:
                role_mentions.append(role.mention)

        embed.add_field(name="Allowed Roles", value=humanize_list(role_mentions) if role_mentions else "None", inline=False)
        await ctx.send(embed=embed)
