import json
import time
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
import discord
from redbot.core import Config, checks, commands

from .handoff import MarketSnapshot, MarketSnapshotError
from .order_intent import MarketBuyApproval, OrderBookSnapshot, OrderIntentError
from .production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST, validate_polymarket_production_manifest,
)
from .security_policy import validate_session_key_policy

CONFIG_IDENTIFIER = 1531372026
PRODUCTION_CAPABILITIES = (
    "account_connect", "deposit_wallet_create", "eligibility", "collateral",
    "order", "cancel", "redeem",
)

GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=4)
CATEGORIES = {
    "politics": ("Politics", "2"),
    "crypto": ("Crypto", "21"),
    "sports": ("Sports", "1"),
}


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


def future_handoff_reasons(market: dict) -> tuple[str, ...]:
    """Return only objective market-level blockers for a future CLOB V2 handoff."""
    reasons = []
    if not market.get("active") or market.get("closed"):
        reasons.append("market is not active")
    if not market.get("enableOrderBook"):
        reasons.append("no order book")
    if not market.get("acceptingOrders"):
        reasons.append("not accepting orders")
    if not _json_list(market.get("clobTokenIds")):
        reasons.append("no CLOB outcome tokens")
    return tuple(reasons)


def technically_handoff_ready(market: dict) -> bool:
    return isinstance(market, dict) and not future_handoff_reasons(market)


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
    __version__ = "0.2.8"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_global(
            production_enabled=False,
            production_paused=True,
            production_capabilities={name: False for name in PRODUCTION_CAPABILITIES},
        )
        self.config.register_user(account_connection=None)

    async def _get_json(self, path: str, params: dict | None = None):
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(GAMMA_API + path, params=params, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Polymarket returned HTTP {response.status}.")
                payload = await response.json(content_type=None)
        return payload

    async def _get_clob_json(self, path: str, params: dict | None = None):
        base = POLYMARKET_PRODUCTION_MANIFEST.clob_api
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(base + path, params=params, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Polymarket CLOB returned HTTP {response.status}.")
                return await response.json(content_type=None)

    @commands.group(aliases=["poly"], invoke_without_command=True)
    async def polymarket(self, ctx: commands.Context):
        """Browse read-only Polymarket market information.

        This cog does not connect wallets, custody funds, sign transactions, or place orders.
        """
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Polymarket discovery",
            description="Public market information only. Market-implied probabilities are not financial advice.",
        )
        embed.add_field(name="Choose a category", value=f"`{prefix}poly markets` or `{prefix}poly markets crypto`\nPolitics, crypto, and sports.", inline=False)
        embed.add_field(name="Search market questions", value=f"`{prefix}poly search <words>`\nExamples: bitcoin, ethereum, fed rates, trump.", inline=False)
        embed.add_field(name="Trending", value=f"`{prefix}poly trending`\nActive markets ranked by 24-hour volume.", inline=False)
        embed.add_field(name="One specific market", value=f"`{prefix}poly market <ID, slug, or Polymarket link>`\nProbabilities, rules, resolution source, and link.", inline=False)
        embed.add_field(name="Future compatibility", value=f"`{prefix}poly compatible [words]` and `{prefix}poly readiness <market>`\nTechnical metadata only; trading is disabled.", inline=False)
        embed.add_field(name="Live approval preview", value=f"`{prefix}poly quote <market> <outcome> <max pUSD> [max price]`\nPublic quote only; nothing is signed or submitted.", inline=False)
        embed.add_field(name="Safety status", value=f"`{prefix}poly status`", inline=False)
        embed.add_field(name="Account connection", value=f"`{prefix}poly account`\nPublic identity status only; protected connection is not available yet.", inline=False)
        embed.set_footer(text="Read-only: no wallets, deposits, signatures, or trading.")
        await ctx.send(embed=embed)

    @polymarket.command(name="markets", aliases=["browse"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_markets(self, ctx: commands.Context, category: str = ""):
        """Choose politics, crypto, or sports markets."""
        if not category:
            await ctx.invoke(self.polymarket_categories)
            return
        category = category.casefold()
        if category not in CATEGORIES:
            await ctx.send(
                f"Choose **politics**, **crypto**, or **sports**. For keywords, try "
                f"`{ctx.clean_prefix}poly search bitcoin`."
            )
            return
        await ctx.invoke(self.polymarket_category, category=category)

    @polymarket.command(name="search", aliases=["find"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_search(self, ctx: commands.Context, *, query: str):
        """Search active market questions, e.g. bitcoin, ethereum, fed rates, or trump."""
        try:
            markets = _active_search_markets(await self._get_json("/public-search", {"q": query.strip()}))
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        terms = query.casefold().split()
        selected = [
            market for market in markets
            if all(term in str(market.get("question", "")).casefold() for term in terms)
        ][:10]
        if not selected:
            await ctx.send("No active Polymarket markets matched that search.")
            return
        await self._send_market_list(ctx, f"Polymarket search: {query.strip()}", selected)

    async def _send_market_list(self, ctx, title, markets):
        embed = discord.Embed(title=title, description="Read-only market-implied probabilities; not financial advice.")
        for market in markets[:10]:
            outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
            probability = " · ".join(
                f"{outcome}: {float(price):.0%}" for outcome, price in zip(outcomes, prices)
                if str(price).replace(".", "", 1).isdigit()
            )
            details = (probability or "Probability unavailable")
            details += "\nID `" + str(market.get("id")) + "` · [Open market](" + market_url(market) + ")"
            embed.add_field(name=str(market.get("question") or "Untitled market")[:256],
                            value=details[:1024], inline=False)
        await ctx.send(embed=embed)

    @polymarket.command(name="categories", aliases=["types"])
    async def polymarket_categories(self, ctx: commands.Context):
        """Show the curated market categories."""
        prefix = ctx.clean_prefix
        lines = [f"**{label}** - `{prefix}poly category {slug}`" for slug, (label, _) in CATEGORIES.items()]
        embed = discord.Embed(
            title="Polymarket categories",
            description="Choose a category instead of browsing unrelated markets.\n\n" + "\n".join(lines),
        )
        embed.set_footer(text=f"Search anything: {prefix}poly search bitcoin")
        await ctx.send(embed=embed)

    @polymarket.command(name="category", aliases=["type"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_category(self, ctx: commands.Context, category: str):
        """List active politics, crypto, or sports markets."""
        selected = CATEGORIES.get(category.casefold())
        if not selected:
            await ctx.send("Choose one of: **politics**, **crypto**, or **sports**.")
            return
        label, tag_id = selected
        try:
            markets = await self._get_json("/markets", {
                "active": "true", "closed": "false", "tag_id": tag_id, "limit": 10,
                "order": "volume24hr", "ascending": "false",
            })
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        if not isinstance(markets, list) or not markets:
            await ctx.send(f"No active {label.lower()} markets were returned.")
            return
        await self._send_market_list(ctx, f"Polymarket: {label}", markets)

    @polymarket.command(name="trending", aliases=["top"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_trending(self, ctx: commands.Context):
        """List active markets ranked by 24-hour volume across all categories."""
        try:
            markets = await self._get_json("/markets", {
                "active": "true", "closed": "false", "limit": 10,
                "order": "volume24hr", "ascending": "false",
            })
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        if not isinstance(markets, list) or not markets:
            await ctx.send("No active Polymarket markets were returned.")
            return
        await self._send_market_list(ctx, "Trending Polymarket markets", markets)

    @polymarket.command(name="compatible", aliases=["ready"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_compatible(self, ctx: commands.Context, *, query: str = ""):
        """List technically order-ready markets for a future Polygon CLOB V2 handoff."""
        try:
            if query.strip():
                markets = _active_search_markets(await self._get_json("/public-search", {"q": query.strip()}))
            else:
                markets = await self._get_json("/markets", {"active": "true", "closed": "false", "limit": 50, "order": "volume24hr", "ascending": "false"})
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send("Polymarket market data could not be reached right now.")
            return
        ready = [market for market in markets if technically_handoff_ready(market)][:10] if isinstance(markets, list) else []
        if not ready:
            await ctx.send("No technically order-ready active Polymarket markets matched that search.")
            return
        embed = discord.Embed(
            title="Future CryptoWallet-compatible markets",
            description="Technical CLOB V2 readiness only—not user eligibility or trading availability.",
        )
        for market in ready:
            outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
            probability = " · ".join(f"{outcome}: {float(price):.0%}" for outcome, price in zip(outcomes, prices) if str(price).replace(".", "", 1).isdigit())
            value = (probability or "Probability unavailable") + f"\nID `{market.get('id')}` · [Open market]({market_url(market)})"
            embed.add_field(name=str(market.get("question") or "Untitled market")[:256], value=value[:1024], inline=False)
        embed.set_footer(text="Future path: Polygon mainnet · pUSD · user-controlled approval · eligibility required")
        await ctx.send(embed=embed)

    @polymarket.command(name="readiness", aliases=["handoff"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_readiness(self, ctx: commands.Context, reference: str):
        """Show public technical readiness for a future disabled Polygon handoff."""
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or link.")
            return
        try:
            market = await self._get_json(path)
            snapshot = MarketSnapshot.from_market(market, quote_timestamp=int(time.time()))
        except (aiohttp.ClientError, RuntimeError, ValueError, MarketSnapshotError):
            await ctx.send("That market is not technically ready for the staged future handoff.")
            return
        embed = discord.Embed(title="Future handoff readiness", url=market_url(market), description=snapshot.question)
        embed.add_field(name="Technical market state", value="Active CLOB market with accepting order book and outcome tokens.", inline=False)
        embed.add_field(name="Future target", value=f"Polygon mainnet (`137`) · pUSD\nSelected-market minimum size: `{snapshot.minimum_order_size or 'not supplied'}`", inline=False)
        embed.add_field(name="Execution state", value="Disabled. No wallet lookup, account creation, balance check, approval, signature, or order occurs.", inline=False)
        embed.set_footer(text="A later protected approval, eligibility, security, and release review is required.")
        await ctx.send(embed=embed)

    @polymarket.command(name="quote")
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_quote(self, ctx: commands.Context, reference: str, outcome: str,
                               max_spend_pusd: str, max_price: str | None = None):
        """Preview a bounded live CLOB market buy without signing or submitting it."""
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or link.")
            return
        now = int(time.time())
        try:
            market = await self._get_json(path)
            snapshot = MarketSnapshot.from_market(market, quote_timestamp=now)
            selected = next((index for index, label in enumerate(snapshot.outcomes)
                             if label.casefold() == outcome.casefold()), None)
            if selected is None and outcome.isdigit() and 1 <= int(outcome) <= len(snapshot.outcomes):
                selected = int(outcome) - 1
            if selected is None:
                raise OrderIntentError("Outcome is not part of this market.")
            token_id = snapshot.outcome_token_ids[selected]
            book_payload = await self._get_clob_json("/book", {"token_id": token_id})
            fee_payload = await self._get_clob_json("/fee-rate", {"token_id": token_id})
            quote = OrderBookSnapshot.from_payload(book_payload, captured_at=now)
            base_fee_bps = int(fee_payload["base_fee"])
            approval = MarketBuyApproval.create(
                requester_id=ctx.author.id, market_id=snapshot.market_id,
                condition_id=snapshot.condition_id, outcome=snapshot.outcomes[selected],
                quote=quote, max_price=max_price or format(quote.best_ask, "f"),
                max_spend_pusd=max_spend_pusd, maximum_base_fee_bps=base_fee_bps,
                expires_at=now + 120,
            )
        except (aiohttp.ClientError, RuntimeError, ValueError, KeyError, TypeError,
                MarketSnapshotError, OrderIntentError):
            await ctx.send("A complete bounded live quote could not be produced. Check the market, outcome, amount, and maximum price.")
            return
        embed = discord.Embed(
            title="Polymarket market-buy preview", url=market_url(market),
            description=snapshot.question,
        )
        embed.add_field(name="Outcome", value=approval.outcome, inline=True)
        embed.add_field(name="Best ask / ceiling", value=f"{quote.best_ask} / {approval.max_price}", inline=True)
        embed.add_field(name="All-in cap", value=f"{approval.max_spend_pusd} pUSD", inline=True)
        embed.add_field(name="Maximum notional", value=f"{approval.maximum_notional} pUSD", inline=True)
        embed.add_field(name="Fee reserve", value=f"up to {approval.maximum_fee_pusd} pUSD (base fee {base_fee_bps} bps)", inline=True)
        embed.add_field(name="Market constraints", value=f"Minimum {quote.minimum_order_size} shares · tick {quote.tick_size} · {'negative-risk' if quote.negative_risk else 'standard'} exchange", inline=False)
        embed.add_field(name="Approval fingerprint", value=f"`{approval.fingerprint}`", inline=False)
        embed.add_field(name="Expires", value=f"<t:{approval.expires_at}:R>", inline=True)
        embed.add_field(name="Execution", value="Preview only. No account, balance, allowance, signature, credential, or order was used.", inline=False)
        await ctx.send(embed=embed)

    @polymarket.command(name="market", aliases=["info"])
    @commands.bot_has_permissions(embed_links=True)
    async def polymarket_market(self, ctx: commands.Context, reference: str):
        """Show outcomes, probabilities, rules, and links from a market ID, slug, or Polymarket link."""
        reference_key = reference.casefold()
        if reference_key in CATEGORIES:
            await ctx.send(
                f"**{reference_key}** is a category. Use "
                f"`{ctx.clean_prefix}poly markets {reference_key}`. "
                f"For keyword matching, use `{ctx.clean_prefix}poly search {reference_key}`."
            )
            return
        path = market_path(reference)
        if not path:
            await ctx.send("Use a Polymarket market ID, slug, or `polymarket.com/event/...` link.")
            return
        try:
            market = await self._get_json(path)
        except (aiohttp.ClientError, RuntimeError, ValueError):
            await ctx.send(
                f"That exact market could not be reached. Use an ID, slug, or Polymarket link, "
                f"or try `{ctx.clean_prefix}poly search {reference}`."
            )
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
        """Show the reviewed, default-off production integration boundary."""
        manifest = POLYMARKET_PRODUCTION_MANIFEST
        drift = validate_polymarket_production_manifest(manifest)
        enabled = bool(await self.config.production_enabled())
        paused = bool(await self.config.production_paused())
        embed = discord.Embed(
            title="Polymarket integration status",
            description="Public discovery is available. Production execution remains disabled.",
        )
        embed.add_field(
            name="Production target",
            value=f"Polygon mainnet (`{manifest.chain_id}`) · {manifest.collateral_symbol} ({manifest.collateral_decimals} decimals)",
            inline=False,
        )
        embed.add_field(
            name="Wallet model",
            value="Signer and Polymarket account wallet are separate identities. New accounts use Deposit Wallets; legacy Proxy and Safe wallets remain explicit types.",
            inline=False,
        )
        embed.add_field(
            name="Reviewed boundary",
            value=(
                f"Manifest: {'valid' if not drift else 'drift detected'} · "
                f"installation: {'enabled' if enabled else 'disabled'} · "
                f"pause: {'active' if paused else 'inactive'} · execution disabled"
            ),
            inline=False,
        )
        embed.set_footer(text="No wallet creation, credentials, approvals, signatures, deposits, or orders")
        await ctx.send(embed=embed)

    @polymarket.command(name="account")
    async def polymarket_account(self, ctx: commands.Context):
        """Show the caller's secret-free Polymarket connection state."""
        record = await self.config.user(ctx.author).account_connection()
        if not record:
            await ctx.send(
                "No Polymarket account is connected. Protected onboarding is still "
                "code-disabled; never send a private key, recovery phrase, signature, "
                "or API credential in Discord."
            )
            return
        await ctx.send(
            "A Polymarket account record exists but cannot be displayed or used until "
            "protected onboarding is reviewed. Production execution remains disabled."
        )

    @commands.group(name="polyset")
    @checks.is_owner()
    async def polymarketset(self, ctx: commands.Context):
        """Owner-only Polymarket production controls."""
        pass

    @polymarketset.command(name="productionstatus")
    async def polymarketset_production_status(self, ctx: commands.Context):
        """Show the default-off Polygon production control state."""
        capabilities = await self.config.production_capabilities()
        enabled = [name for name in PRODUCTION_CAPABILITIES if capabilities.get(name)]
        await ctx.send(
            "**Polymarket Polygon production**\n"
            f"Manifest: **{'valid' if not validate_polymarket_production_manifest() else 'drift detected'}**\n"
            f"Installation enabled: **{bool(await self.config.production_enabled())}**\n"
            f"Emergency paused: **{bool(await self.config.production_paused())}**\n"
            f"Enabled capabilities: **{', '.join(enabled) if enabled else 'none'}**\n"
            f"Session-key policy: **{'valid, beta, non-executable' if not validate_session_key_policy() else 'drift detected'}**\n"
            "Order execution: **code-disabled**"
        )

    @polymarketset.command(name="productioncontrol")
    async def polymarketset_production_control(self, ctx: commands.Context, mode: str):
        """Pause the production boundary; enablement fails closed until reviewed."""
        choice = str(mode or "").strip().lower()
        if choice in {"pause", "disable"}:
            await self.config.production_enabled.set(False)
            await self.config.production_paused.set(True)
            await ctx.send("Polymarket production is disabled and emergency-paused.")
            return
        if choice != "enable":
            await ctx.send("Use `productioncontrol enable`, `pause`, or `disable`.")
            return
        if (
            validate_polymarket_production_manifest()
            or not POLYMARKET_PRODUCTION_MANIFEST.execution_enabled
            or not POLYMARKET_PRODUCTION_MANIFEST.executable_capabilities
        ):
            await ctx.send(
                "Polymarket production remains code-disabled. No state changed."
            )
            return
        await ctx.send("Polymarket production cannot be enabled by this release.")
