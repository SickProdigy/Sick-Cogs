import asyncio
import json
import random
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
import logging
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

import aiohttp
import discord

from redbot.core import commands
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu


log = logging.getLogger("red.sick-cogs.wolfram")

EXAMPLES_URL = "https://www.wolframalpha.com/examples"
EXAMPLE_CATEGORY_URLS = {
    "mathematics": f"{EXAMPLES_URL}/mathematics",
    "science": f"{EXAMPLES_URL}/science",
    "society": f"{EXAMPLES_URL}/society",
    "everyday": f"{EXAMPLES_URL}/everyday-life",
    "surprises": f"{EXAMPLES_URL}/surprises",
}


def load_examples(path: Path) -> Dict[str, List[str]]:
    """Load a small, deliberately curated catalog of safe example queries."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        categories = raw["categories"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Wolfram example catalog is invalid.") from exc
    if not isinstance(categories, dict):
        raise ValueError("Wolfram example catalog categories must be an object.")
    validated = {}
    for category, examples in categories.items():
        if category not in EXAMPLE_CATEGORY_URLS or not isinstance(examples, list):
            raise ValueError("Wolfram example catalog has an unsupported category.")
        queries = [query.strip() for query in examples if isinstance(query, str) and query.strip()]
        if not queries or len(queries) != len(examples):
            raise ValueError("Wolfram example catalog has an invalid query.")
        validated[category] = queries
    if set(validated) != set(EXAMPLE_CATEGORY_URLS):
        raise ValueError("Wolfram example catalog is incomplete.")
    return validated


class RequestFailure(Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER = "provider"
    NETWORK = "network"


@dataclass(frozen=True)
class WolframResponse:
    body: Optional[bytes] = None
    content_type: str = ""
    failure: Optional[RequestFailure] = None
    status: Optional[int] = None


class Wolfram(commands.Cog):
    """Ask Wolfram|Alpha any question."""

    __version__ = "2.3.0"

    API_BASE_URL = "https://api.wolframalpha.com"
    DEVELOPER_URL = "https://products.wolframalpha.com/api/"

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.examples = load_examples(Path(__file__).with_name("data") / "examples.json")
        self._last_example: Optional[str] = None

    def choose_example(self, category: Optional[str] = None) -> Tuple[str, str]:
        """Choose a catalog entry while avoiding an immediate repeat when possible."""
        normalized = category.casefold().strip() if category else None
        if normalized is not None and normalized not in self.examples:
            raise ValueError("Unknown Wolfram example category.")
        candidates = [(name, query) for name, queries in self.examples.items() for query in queries]
        if normalized is not None:
            candidates = [(normalized, query) for query in self.examples[normalized]]
        alternatives = [item for item in candidates if item[1] != self._last_example]
        chosen = random.choice(alternatives or candidates)
        self._last_example = chosen[1]
        return chosen

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
        """Request a Wolfram endpoint and return structured success or failure details."""
        headers = {"User-Agent": f"Sick-Cogs-Wolfram/{self.__version__}"}
        try:
            async with self.session.get(
                f"{self.API_BASE_URL}{path}", params=params, headers=headers
            ) as response:
                status = response.status
                if status in {401, 403, 501}:
                    log.warning("Wolfram|Alpha rejected the configured AppID (HTTP %s).", status)
                    return WolframResponse(failure=RequestFailure.AUTHENTICATION, status=status)
                if status == 429:
                    log.warning("Wolfram|Alpha rate limited a request (HTTP 429).")
                    return WolframResponse(failure=RequestFailure.RATE_LIMIT, status=status)
                if status >= 400:
                    log.warning("Wolfram|Alpha returned HTTP status %s.", status)
                    return WolframResponse(failure=RequestFailure.PROVIDER, status=status)
                return WolframResponse(
                    body=await response.read(),
                    content_type=response.headers.get("Content-Type", ""),
                    status=status,
                )
        except asyncio.TimeoutError:
            log.warning("Wolfram|Alpha request timed out.")
            return WolframResponse(failure=RequestFailure.TIMEOUT)
        except aiohttp.ClientError:
            log.warning("Wolfram|Alpha request failed.")
            return WolframResponse(failure=RequestFailure.NETWORK)

    async def _send_request_failure(self, ctx, failure):
        if failure is RequestFailure.AUTHENTICATION:
            return await ctx.send(
                "Wolfram|Alpha rejected the configured AppID. The bot owner should check or "
                f"replace it with `{ctx.clean_prefix}set api wolfram appid,APP_ID`."
            )
        if failure is RequestFailure.RATE_LIMIT:
            return await ctx.send("Wolfram|Alpha is rate limiting requests. Please try again later.")
        if failure is RequestFailure.TIMEOUT:
            return await ctx.send("Wolfram|Alpha took too long to respond. Please try again later.")
        if failure is RequestFailure.NETWORK:
            return await ctx.send("Wolfram|Alpha could not be reached. Please try again later.")
        return await ctx.send("Wolfram|Alpha is temporarily unavailable. Please try again later.")

    @commands.command(name="wolframexample", aliases=["wolframrandom"])
    async def wolfram_example(self, ctx, *, category: Optional[str] = None):
        """Show a curated Wolfram example.

        Categories: mathematics, science, society, everyday, surprises.
        Run the shown query with `[p]wolfram <query>` when you want an answer.
        """
        try:
            chosen_category, query = self.choose_example(category)
        except ValueError:
            choices = ", ".join(EXAMPLE_CATEGORY_URLS)
            await ctx.send(f"Unknown category. Choose one of: {choices}.")
            return
        prefix = ctx.clean_prefix
        title = f"Wolfram example: {chosen_category.title()}"
        embed = discord.Embed(
            title=title,
            url=EXAMPLE_CATEGORY_URLS[chosen_category],
            description=f"`{query}`",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Try it",
            value=f"Run `{prefix}wolfram {query}` to ask Wolfram|Alpha.",
            inline=False,
        )
        embed.set_footer(text="Curated example • Source: Wolfram|Alpha Examples")
        await ctx.send(embed=embed)

    @commands.command(name="wolfram")
    async def _wolfram(self, ctx, *question: str):
        """Ask Wolfram|Alpha a factual, mathematical, or scientific question.

        Examples:
        - `[p]wolfram 2+2`
        - `[p]wolfram population of Japan`
        - `[p]wolfram derivative of x^3`

        Related commands:
        - `[p]wolframimage <question>` returns Wolfram|Alpha's visual result.
        - `[p]wolframsolve <question>` requests step-by-step math output.

        A Wolfram|Alpha AppID must be configured by the bot owner.
        """
        if not question or not any(part.strip() for part in question):
            return await ctx.send_help(ctx.command)

        api_key = await self._get_api_key(ctx)
        if not api_key:
            return

        query = " ".join(question)
        async with ctx.typing():
            result = await self._request(
                "/v2/query",
                params={"input": query, "appid": api_key, "format": "plaintext"},
            )
            if result.failure:
                return await self._send_request_failure(ctx, result.failure)
            try:
                root = ET.fromstring(result.body or b"")
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
            if result.failure:
                return await self._send_request_failure(ctx, result.failure)
            image_data = result.body or b""
            content_type = result.content_type
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
            if result.failure:
                return await self._send_request_failure(ctx, result.failure)
            try:
                root = ET.fromstring(result.body or b"")
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
