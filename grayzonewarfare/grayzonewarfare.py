import asyncio
import logging
import time
from urllib.parse import quote

import aiohttp
import discord
from redbot.core import commands

from .utils import TTLCache,normalize_text,parse_page,rank_pages,record_page


log=logging.getLogger("red.sick-cogs.GrayZoneWarfare")
VERSION="0.1.0"
WIKI_NAME="Gray Zone Warfare Wiki"
WIKI_BASE="https://gray-zone-warfare.fandom.com"
API_NAME="GZW Data API"
API_URL="https://gzw-data.dev/api/v1/tasks"
USER_AGENT=f"Sick-Cogs-GrayZoneWarfare/{VERSION} (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"


class WikiUnavailable(RuntimeError):
    pass


class QuestResultSelect(discord.ui.Select):
    def __init__(self,cog,author_id,pages):
        self.cog=cog;self.author_id=author_id;self.pages=pages
        options=[discord.SelectOption(label=str(page.get("title") or "Quest")[:100],value=str(index)) for index,page in enumerate(pages[:5])]
        super().__init__(placeholder="Choose the matching quest…",options=options)

    async def callback(self,interaction):
        if interaction.user.id!=self.author_id:
            await interaction.response.send_message("Run the command yourself to choose a quest.",ephemeral=True);return
        page=self.pages[int(self.values[0])]
        await interaction.response.edit_message(embed=self.cog.quest_embed(page),view=self.view)


class QuestResultView(discord.ui.View):
    def __init__(self,cog,author_id,pages):
        super().__init__(timeout=90);self.message=None;self.add_item(QuestResultSelect(cog,author_id,pages))

    async def on_timeout(self):
        for child in self.children:child.disabled=True
        if self.message:
            try:await self.message.edit(view=self)
            except discord.HTTPException:pass


class GrayZoneWarfare(commands.Cog):
    """Source-linked Gray Zone Warfare quest assistance."""

    __version__=VERSION

    def __init__(self,bot):
        self.bot=bot;self.cache=TTLCache(ttl=600,max_entries=128);self.request_lock=asyncio.Lock();self.last_request=0.0
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),headers={"User-Agent":USER_AGENT})

    async def red_delete_data_for_user(self,**kwargs):
        """Nothing to delete."""
        return

    def cog_unload(self):
        self.cache.clear();asyncio.create_task(self.session.close())

    @commands.group(name="grayzonewarfare",aliases=["gzw"],invoke_without_command=True)
    async def grayzonewarfare(self,ctx):
        """Find Gray Zone Warfare quest guidance from the wiki."""
        await ctx.send_help()

    @grayzonewarfare.command(name="quest",aliases=["task","mission"])
    @commands.bot_has_permissions(embed_links=True)
    @commands.cooldown(1,3,commands.BucketType.user)
    async def quest(self,ctx,*,query:str):
        """Find a quest by exact name or partial keywords."""
        query=" ".join(query.split())
        if not 2<=len(query)<=100:
            await ctx.send("Quest searches must be between 2 and 100 characters.");return
        try:
            async with ctx.typing():pages=await self.search_quests(query)
        except WikiUnavailable:
            log.warning("GZW Data API request failed",exc_info=True)
            await ctx.send("I could not reach the GZW Data API. No quest steps were guessed; try again later.");return
        if not pages:
            search_url=f"{WIKI_BASE}/wiki/Special:Search?query={quote(query)}"
            await ctx.send(f"No quest pages matched **{query}**. Try the [full wiki search]({search_url}).");return
        exact=normalize_text(pages[0].get("title"))==normalize_text(query)
        choices=pages[:5];view=QuestResultView(self,ctx.author.id,choices) if len(choices)>1 and not exact else None
        message=await ctx.send(embed=self.quest_embed(choices[0]),view=view)
        if view:view.message=message

    async def search_quests(self,query):
        key=normalize_text(query);cached=self.cache.get(key)
        if cached is not None:return cached
        catalog=self.cache.get("__catalog__")
        if catalog is None:
            records=[];data_version="";page_number=1
            try:
                async with self.request_lock:
                    while page_number<=5:
                        delay=max(0.0,0.75-(time.monotonic()-self.last_request))
                        if delay:await asyncio.sleep(delay)
                        params={"page":str(page_number),"per_page":"100"}
                        async with self.session.get(API_URL,params=params) as response:
                            self.last_request=time.monotonic()
                            if response.status!=200:raise WikiUnavailable(f"HTTP {response.status}")
                            payload=await response.json(content_type=None)
                        page_records=payload.get("data",[])
                        if not isinstance(page_records,list):raise WikiUnavailable("Unexpected API response")
                        records.extend(page_records);data_version=data_version or payload.get("dataVersion","")
                        total_pages=max(1,int(payload.get("totalPages",1) or 1))
                        if page_number>=total_pages:break
                        page_number+=1
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError) as exc:
                raise WikiUnavailable("API request failed") from exc
            catalog=[record_page(record,data_version) for record in records]
            self.cache.set("__catalog__",catalog)
        ranked=rank_pages(query,catalog);self.cache.set(key,ranked);return ranked

    @staticmethod
    def quest_embed(page):
        quest=parse_page(page);description=[]
        if quest["type"]:description.append("**{}**".format(quest["type"]))
        if quest["faction_specific"]:description.append("Briefing or location wording differs by faction; check the linked page for your faction.")
        if not any((quest["vendor"],quest["location"],quest["objectives"],quest["guide"])):
            description.append("The data source returned this quest without structured guidance. Open the linked wiki page for current details.")
        embed=discord.Embed(title=quest["title"][:256],url=quest["url"] or None,description="\n".join(description)[:4096],colour=discord.Colour.from_rgb(191,124,46))
        if quest["vendor"]:embed.add_field(name="Task giver",value=quest["vendor"][:1024],inline=True)
        if quest["location"]:embed.add_field(name="Location",value=quest["location"][:1024],inline=True)
        if quest["objectives"]:
            value="\n".join(f"• {item}" for item in quest["objectives"]);embed.add_field(name="Objectives",value=value[:1024],inline=False)
        if quest["guide"]:embed.add_field(name="Briefing",value=quest["guide"][:1024],inline=False)
        updated=quest["updated"][:10] if quest["updated"] else "unknown"
        embed.set_footer(text=f"Source: {API_NAME} + {WIKI_NAME} • Data snapshot: {updated} • Guidance may change after patches")
        return embed
