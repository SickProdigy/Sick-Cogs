import json
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
import discord
from redbot.core import commands

GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=4)


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def market_url(market: dict) -> str:
    slug = market.get("slug")
    return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"


def _active_search_markets(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    markets = []
    seen = set()
    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        for market in event.get("markets", []):
            if not isinstance(market, dict) or not market.get("active") or market.get("closed"):
                continue
            market_id = market.get("id")
            if market_id in seen:
                continue
            seen.add(market_id)
            markets.append(market)
    return markets


def market_path(reference: str) -> str | None:
    value = reference.strip()
    if value.isdigit():
        return f"/markets/{value}"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc.casefold() not in {"polymarket.com", "www.polymarket.com"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0] not in {"event", "market"}:
            return None
        value = parts[1]
    if not value or "/" in value or any(char.isspace() for char in value):
        return None
    return f"/markets/slug/{quote(value, safe='-_')}"


class Polymarket(commands.Cog):
    """Read-only prediction-market discovery and information."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.8"

    def __init__(self, bot):
        self.bot = bot

    async def _get_json(self, path: str, params: dict | None = None):
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(GAMMA_API + path, params=params, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Polymarket returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        return payload

    @commands.group(invoke_without_command=True)
    async def polymarket(self, ctx: commands.Context):
        """Browse read-only Polymarket market information.

        This cog does not connect wallets, custody funds, sign transactions, or place orders.
        """
        embed = discord.Embed(
            title="Polymarket discovery",
            description="Public market information only. Market-implied probabilities are not financial advice.",
        )
        embed.add_field(name="Browse active markets", value="`polymarket markets`\nRanked by 24-hour volume.", inline=False)
        embed.add_field(name="Search", value="`polymarket markets <words>`\nExample: `polymarket markets bitcoin`", inline=False)
        embed.add_field(name="Market details", value="`polymarket market <ID, slug, or Polymarket link>`\nShows probabilities, rules, resolution source, and the canonical link.", inline=False)
        embed.set_footer(text="Read-only: no wallets, deposits, signatures, or trading.")
        await ctx.send(embed=embed)

    @polymarket.command(name="markets", aliases=["search", "browse", "trending"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_markets(self, ctx: commands.Context, *, query: str = ""):
        """List active markets ranked by 24-hour volume, optionally filtered by question words."""
        try:
            if query.strip():
                markets = _active_search_markets(await self._get_json("/public-search", {"q": query.strip()}))
            else:
                markets = await self._get_json("/markets", {"active": "true", "closed": "false", "limit": 50, "order": "volume24hr", "ascending": "false"})
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        if not isinstance(markets, list):
            await ctx.send("Polymarket returned an unexpected market response.")
            return
        terms = query.casefold().split()
        filtered = [m for m in markets if isinstance(m, dict) and all(term in str(m.get("question", "")).casefold() for term in terms)]
        selected = filtered[:10]
        if not selected:
            await ctx.send("No active Polymarket markets matched that search.")
            return
        embed = discord.Embed(title="Polymarket markets", description="Read-only market-implied probabilities; not financial advice.")
        for market in selected:
            outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
            probability = " · ".join(f"{outcome}: {float(price):.0%}" for outcome, price in zip(outcomes, prices) if str(price).replace(".", "", 1).isdigit())
            details = probability or "Probability unavailable"
            details += f"\nID `{market.get('id')}` · [Open market]({market_url(market)})"
            embed.add_field(name=str(market.get("question") or "Untitled market")[:256], value=details[:1024], inline=False)
        await ctx.send(embed=embed)

    @polymarket.command(name="market", aliases=["info"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_market(self, ctx: commands.Context, reference: str):
        """Show outcomes, probabilities, rules, and links from a market ID, slug, or Polymarket link."""
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or `polymarket.com/event/...` link.")
            return
        try:
            market = await self._get_json(path)
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("That Polymarket market could not be reached. Use an ID, slug, or Polymarket link from `polymarket markets`.")
            return
        if not isinstance(market, dict):
            await ctx.send("Polymarket returned an unexpected market response.")
            return
        embed = discord.Embed(title=str(market.get("question") or "Polymarket market"), url=market_url(market), description=str(market.get("description") or "No market description was supplied.")[:4096])
        outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
        lines = []
        for outcome, price in zip(outcomes, prices):
            try:
                lines.append(f"{outcome}: **{float(price):.1%}**")
            except (TypeError, ValueError):
                lines.append(f"{outcome}: unavailable")
        embed.add_field(name="Market-implied probabilities", value="\n".join(lines) or "Unavailable", inline=False)
        if market.get("resolutionSource"):
            embed.add_field(name="Resolution source", value=str(market["resolutionSource"])[:1024], inline=False)
        details = []
        if market.get("endDateIso") or market.get("endDate"):
            details.append("Closes: " + str(market.get("endDateIso") or market.get("endDate")))
        if market.get("volume"):
            details.append("Volume: " + str(market["volume"]))
        if market.get("liquidity"):
            details.append("Liquidity: " + str(market["liquidity"]))
        embed.set_footer(text=" · ".join(details) or "Read-only Polymarket data")
        await ctx.send(embed=embed)

    @polymarket.command(name="status")
    async def polymarket_status(self, ctx: commands.Context):
        """Show the current implementation boundary."""
        await ctx.send("**Polymarket cog status**\nPhase: read-only discovery\nAvailable: market discovery and details\nNot available: wallets, deposits, signatures, or trading.")
