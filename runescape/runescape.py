import asyncio
import logging
from urllib.parse import quote

import aiohttp
import discord
from redbot.core import commands

from .utils import OSRS_SKILLS, RS3_SKILLS, clean_extract, parse_hiscores, wiki_page_url

log = logging.getLogger("red.Sick-Cogs.RuneScape")
USER_AGENT = "Sick-Cogs RuneScape cog/1.0 (https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"
OSRS_HISCORES = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws"
RS3_HISCORES = "https://secure.runescape.com/m=hiscore/index_lite.ws"
WIKIS = {
    "osrs": ("Old School RuneScape", "https://oldschool.runescape.wiki"),
    "rs3": ("RuneScape", "https://runescape.wiki"),
}


class WikiResultSelect(discord.ui.Select):
    def __init__(self, cog, author_id: int, wiki_key: str, pages: list[dict]):
        self.cog = cog
        self.author_id = author_id
        self.wiki_key = wiki_key
        self.pages = pages
        options = [
            discord.SelectOption(label=page["title"][:100], value=str(index))
            for index, page in enumerate(pages)
        ]
        super().__init__(placeholder="Choose the closest wiki result…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Run the command yourself to choose a result.", ephemeral=True
            )
            return
        page = self.pages[int(self.values[0])]
        await interaction.response.edit_message(
            embed=self.cog.wiki_embed(self.wiki_key, page), view=self.view
        )


class WikiResultView(discord.ui.View):
    def __init__(self, cog, author_id: int, wiki_key: str, pages: list[dict]):
        super().__init__(timeout=90)
        self.message = None
        self.add_item(WikiResultSelect(cog, author_id, wiki_key, pages))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class RuneScape(commands.Cog):
    """Player hiscores and wiki information for RuneScape."""

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": USER_AGENT}
        )

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.group(name="runescape", aliases=["rs"], invoke_without_command=True)
    async def runescape(self, ctx: commands.Context):
        """Look up RuneScape players and wiki articles."""
        await ctx.send_help()

    @runescape.command(name="user", aliases=["player", "stats"])
    async def user(self, ctx: commands.Context, *, query: str):
        """Show stats. Prefix a name with `rs3` to use RuneScape 3."""
        game, username = self._split_game(query)
        if not username or len(username) > 12:
            await ctx.send("RuneScape usernames must be between 1 and 12 characters.")
            return

        endpoint = RS3_HISCORES if game == "rs3" else OSRS_HISCORES
        skills = RS3_SKILLS if game == "rs3" else OSRS_SKILLS
        try:
            async with ctx.typing():
                async with self.session.get(endpoint, params={"player": username}) as response:
                    if response.status == 404:
                        await ctx.send(
                            f"No public {WIKIS[game][0]} hiscores were found for **{username}**."
                        )
                        return
                    if response.status != 200:
                        await ctx.send(f"Jagex returned HTTP {response.status}; try again later.")
                        return
                    rows = parse_hiscores(await response.text(), skills)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.warning("Jagex hiscores request failed", exc_info=True)
            await ctx.send("I couldn't reach the Jagex hiscores service. Try again later.")
            return

        if not rows:
            await ctx.send("Jagex returned an unexpected hiscores response.")
            return
        await ctx.send(embed=self.player_embed(game, username, rows))

    @runescape.command(name="wiki", aliases=["search"])
    async def wiki(self, ctx: commands.Context, *, query: str):
        """Search the Old School RuneScape Wiki."""
        await self._wiki_search(ctx, "osrs", query)

    @runescape.command(name="rs3wiki")
    async def rs3wiki(self, ctx: commands.Context, *, query: str):
        """Search the RuneScape (RS3) Wiki."""
        await self._wiki_search(ctx, "rs3", query)

    @runescape.command(name="item")
    async def item(self, ctx: commands.Context, *, name: str):
        """Find an item on the Old School RuneScape Wiki."""
        await self._wiki_search(ctx, "osrs", name)

    @runescape.command(name="quest")
    async def quest(self, ctx: commands.Context, *, name: str):
        """Find a quest on the Old School RuneScape Wiki."""
        await self._wiki_search(ctx, "osrs", name)

    async def _wiki_search(self, ctx: commands.Context, wiki_key: str, query: str):
        query = query.strip()
        if len(query) < 2 or len(query) > 100:
            await ctx.send("Wiki searches must be between 2 and 100 characters.")
            return

        wiki_name, base_url = WIKIS[wiki_key]
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "0",
            "gsrlimit": "5",
            "prop": "extracts|pageimages|info",
            "exintro": "1",
            "explaintext": "1",
            "exchars": "1000",
            "piprop": "thumbnail",
            "pithumbsize": "500",
            "inprop": "url",
            "format": "json",
            "formatversion": "2",
        }
        try:
            async with ctx.typing():
                async with self.session.get(f"{base_url}/api.php", params=params) as response:
                    if response.status != 200:
                        await ctx.send(f"The {wiki_name} Wiki returned HTTP {response.status}.")
                        return
                    payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            log.warning("RuneScape Wiki request failed", exc_info=True)
            await ctx.send(f"I couldn't reach the {wiki_name} Wiki. Try again later.")
            return

        pages = sorted(
            payload.get("query", {}).get("pages", []),
            key=lambda page: page.get("index", 999),
        )
        if not pages:
            search_url = f"{base_url}/w/Special:Search?search={quote(query)}"
            await ctx.send(
                f"No wiki pages matched **{query}**. Try the [full search]({search_url})."
            )
            return

        view = WikiResultView(self, ctx.author.id, wiki_key, pages) if len(pages) > 1 else None
        message = await ctx.send(embed=self.wiki_embed(wiki_key, pages[0]), view=view)
        if view:
            view.message = message

    @staticmethod
    def _split_game(query: str) -> tuple[str, str]:
        parts = query.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() in {"rs3", "osrs"}:
            return parts[0].lower(), parts[1].strip()
        return "osrs", query.strip()

    @staticmethod
    def player_embed(game: str, username: str, rows: list[dict]) -> discord.Embed:
        overall, *skill_rows = rows
        hiscores_url = (
            "https://secure.runescape.com/m=hiscore_oldschool/overall"
            if game == "osrs"
            else "https://secure.runescape.com/m=hiscore/ranking"
        )
        embed = discord.Embed(
            title=f"{username} — {WIKIS[game][0]}",
            url=hiscores_url,
            colour=discord.Colour.gold(),
        )
        embed.description = (
            f"**Total level:** {overall['level']:,}\n"
            f"**Overall XP:** {overall['experience']:,}\n"
            f"**Overall rank:** {overall['rank']:,}"
        )
        for index, column in enumerate(skill_rows[offset::3] for offset in range(3)):
            if column:
                value = "\n".join(
                    f"**{row['name']}** {row['level']:,}" for row in column
                )
                embed.add_field(
                    name="Skills" if index == 0 else "\u200b", value=value, inline=True
                )
        embed.set_footer(text="Data supplied by the Jagex hiscores service")
        return embed

    @staticmethod
    def wiki_embed(wiki_key: str, page: dict) -> discord.Embed:
        wiki_name, base_url = WIKIS[wiki_key]
        title = page.get("title", "Wiki result")
        embed = discord.Embed(
            title=title,
            url=page.get("fullurl") or wiki_page_url(base_url, title),
            description=clean_extract(page.get("extract", "")),
            colour=discord.Colour.green(),
        )
        thumbnail = page.get("thumbnail", {}).get("source")
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text=f"Source: {wiki_name} Wiki")
        return embed
