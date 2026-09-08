import asyncio
from io import BytesIO
import logging
import xml.etree.ElementTree as ET

import aiohttp
import discord

from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu


log = logging.getLogger("red.sick-cogs.wolfram")


class Wolfram(commands.Cog):
    """Ask Wolfram|Alpha any question."""

    __version__ = "2.1.0"

    API_BASE_URL = "https://api.wolframalpha.com"
    DEVELOPER_URL = "https://products.wolframalpha.com/api/"

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        default_global = {"WOLFRAM_API_KEY": None}
        self.config = Config.get_conf(self, 2788801004, force_registration=True)
        self.config.register_global(**default_global)
        # Retain the old guild registration long enough to migrate keys written by v2.0.1.
        self.config.register_guild(**default_global)

    async def cog_load(self):
        """Migrate legacy Config AppIDs to Red's shared API-token storage."""
        shared_tokens = await self.bot.get_shared_api_tokens("wolfram")
        global_key = await self.config.WOLFRAM_API_KEY()
        guild_data = await self.config.all_guilds()
        legacy_keys = {
            key
            for key in (
                global_key,
                *(data.get("WOLFRAM_API_KEY") for data in guild_data.values()),
            )
            if key
        }

        if "appid" not in shared_tokens and len(legacy_keys) == 1:
            await self.bot.set_shared_api_tokens("wolfram", appid=legacy_keys.pop())
            shared_tokens = {"appid": True}
            log.info("Migrated a legacy Wolfram AppID to Red's shared API-token storage.")
        elif "appid" not in shared_tokens and len(legacy_keys) > 1:
            log.warning(
                "Found conflicting legacy Wolfram AppIDs; use Red's set api command to "
                "select the AppID. No credential values were logged."
            )

        if "appid" in shared_tokens:
            await self.config.WOLFRAM_API_KEY.clear()
            for guild_id, data in guild_data.items():
                if data.get("WOLFRAM_API_KEY"):
                    await self.config.guild_from_id(guild_id).WOLFRAM_API_KEY.clear()

    async def _get_api_key(self, ctx):
        api_tokens = await self.bot.get_shared_api_tokens("wolfram")
        api_key = api_tokens.get("appid")
        if not api_key:
            await ctx.send(
                "No Wolfram|Alpha AppID is set. The bot owner can add one with "
                f"`{ctx.clean_prefix}set api wolfram appid,APP_ID`. "
                f"Get an AppID at {self.DEVELOPER_URL}"
            )
        return api_key

    async def _request(self, path, *, params):
        """Request a Wolfram endpoint and return its response body and content type."""
        headers = {"User-Agent": f"Sick-Cogs-Wolfram/{self.__version__}"}
        try:
            async with self.session.get(
                f"{self.API_BASE_URL}{path}", params=params, headers=headers
            ) as response:
                response.raise_for_status()
                return await response.read(), response.headers.get("Content-Type", "")
        except asyncio.TimeoutError:
            log.warning("Wolfram|Alpha request timed out.")
        except aiohttp.ClientResponseError as exc:
            log.warning("Wolfram|Alpha returned HTTP status %s.", exc.status)
        except aiohttp.ClientError:
            log.warning("Wolfram|Alpha request failed.")
        return None

    @commands.command(name="wolfram")
    async def _wolfram(self, ctx, *question: str):
        """Ask Wolfram|Alpha any question."""
        api_key = await self._get_api_key(ctx)
        if not api_key:
            return

        query = " ".join(question)
        async with ctx.typing():
            result = await self._request(
                "/v2/query",
                params={"input": query, "appid": api_key, "format": "plaintext"},
            )
            if result is None:
                return await ctx.send("Wolfram|Alpha could not be reached. Please try again later.")
            try:
                root = ET.fromstring(result[0])
            except ET.ParseError:
                log.warning("Wolfram|Alpha returned malformed XML for a text query.")
                return await ctx.send("Wolfram|Alpha returned an invalid response.")

            answers = []
            for plaintext in root.findall(".//plaintext"):
                if plaintext.text:
                    answers.append(plaintext.text.capitalize())

        if not answers:
            message = "There is as yet insufficient data for a meaningful answer."
        else:
            message = "\n".join(answers[:3])
            if "Current geoip location" in message:
                message = "There is as yet insufficient data for a meaningful answer."

        if len(message) > 1990:
            pages = [box(page) for page in pagify(message, delims=[" | ", "\n"], page_length=1990)]
            await menu(ctx, pages, DEFAULT_CONTROLS)
        else:
            await ctx.send(box(message))

    @commands.command(name="wolframimage")
    async def _image(self, ctx, *arguments: str):
        """Ask Wolfram|Alpha a question and return an image."""
        if not arguments:
            return await ctx.send_help()
        api_key = await self._get_api_key(ctx)
        if not api_key:
            return

        params = {
            "appid": api_key,
            "i": " ".join(arguments),
            "width": 800,
            "fontsize": 30,
            "layout": "labelbar",
            "background": "193555",
            "foreground": "white",
            "units": "metric",
        }

        async with ctx.typing():
            result = await self._request("/v1/simple", params=params)
            if result is None:
                return await ctx.send("Wolfram|Alpha could not be reached. Please try again later.")
            image_data, content_type = result
            if not content_type.lower().startswith("image/"):
                return await ctx.send(
                    "There is as yet insufficient data for a meaningful answer."
                )
            try:
                await ctx.send(
                    file=discord.File(BytesIO(image_data), f"wolfram{ctx.author.id}.png")
                )
            except discord.HTTPException:
                log.exception("Discord rejected a Wolfram|Alpha image response.")
                await ctx.send("I couldn't send the Wolfram|Alpha image.")

    @commands.command(name="wolframsolve")
    async def _solve(self, ctx, *, query: str):
        """Ask Wolfram|Alpha a math question and return step-by-step answers."""
        api_key = await self._get_api_key(ctx)
        if not api_key:
            return

        params = {
            "appid": api_key,
            "input": query,
            "podstate": "Step-by-step solution",
            "format": "plaintext",
        }

        async with ctx.typing():
            result = await self._request("/v2/query", params=params)
            if result is None:
                return await ctx.send("Wolfram|Alpha could not be reached. Please try again later.")
            try:
                root = ET.fromstring(result[0])
            except ET.ParseError:
                log.warning("Wolfram|Alpha returned malformed XML for a step-by-step query.")
                return await ctx.send("Wolfram|Alpha returned an invalid response.")

            message = ""
            for pod in root.findall(".//pod"):
                title = pod.attrib.get("title", "Result")
                if title == "Number line":
                    continue
                message += f"{title}\n"
                for plaintext in pod.findall(".//plaintext"):
                    if plaintext.text:
                        cleaned = plaintext.text.replace(" | ", " ").replace("| ", " ")
                        message += f"- {cleaned}\n\n"

            if not message:
                message = "There is as yet insufficient data for a meaningful answer."
            for page in pagify(message):
                await ctx.send(box(page))

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())
