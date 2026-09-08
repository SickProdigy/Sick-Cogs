import logging
import re
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from redbot.core import commands


log = logging.getLogger("red.sick-cogs.Dictionary")

API_BASE = "https://api.dictionaryapi.dev/api/v2/entries/en"
URBAN_API_URL = "https://api.urbandictionary.com/v0/define"
PROVIDER_URL = "https://dictionaryapi.dev/"
URBAN_PROVIDER_URL = "https://www.urbandictionary.com/"
USER_AGENT = "Sick-Cogs-Dictionary/2.0.0 (+https://github.com/SickProdigy/Sick-Cogs)"
MAX_ENTRIES = 3
MAX_MEANINGS = 4
MAX_DEFINITIONS = 3
MAX_RELATED_WORDS = 25


class UrbanDictionaryView(discord.ui.View):
    """Requester-bound pagination for Urban Dictionary results."""

    def __init__(self, author_id: int, embeds: list[discord.Embed]):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.embeds = embeds
        self.index = 0
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.embeds) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Only the person who requested this lookup can change its result page.", ephemeral=True
        )
        return False

    async def _show_page(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        await self._show_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.index = min(len(self.embeds) - 1, self.index + 1)
        await self._show_page(interaction)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class Dictionary(commands.Cog):
    """Look up English definitions and related words."""

    __author__ = ["SickProdigy"]
    __version__ = "2.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def red_delete_data_for_user(self, **kwargs):
        """This cog does not store user data."""
        return

    def cog_unload(self):
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        return self.session

    @staticmethod
    def _clean_term(term: str) -> str:
        return " ".join(term.strip().split())

    async def _lookup(self, term: str) -> tuple[Optional[list[dict]], Optional[str]]:
        session = await self._get_session()
        url = f"{API_BASE}/{quote(term, safe='')}"
        try:
            async with session.get(url) as response:
                if response.status == 404:
                    return None, f"I could not find an English dictionary entry for **{term}**."
                if response.status != 200:
                    return None, f"The dictionary service returned HTTP {response.status}. Please try again later."
                payload = await response.json(content_type=None)
        except aiohttp.ClientError:
            log.warning("Dictionary API request failed for %r", term, exc_info=True)
            return None, "I could not reach the dictionary service. Please try again later."
        except Exception:
            log.exception("Unexpected dictionary lookup failure for %r", term)
            return None, "Something unexpected went wrong during that dictionary lookup."

        if not isinstance(payload, list) or not payload:
            return None, f"I could not find an English dictionary entry for **{term}**."
        return [entry for entry in payload if isinstance(entry, dict)], None

    @staticmethod
    def _unique_words(values) -> list[str]:
        seen = set()
        words = []
        for value in values:
            word = str(value).strip()
            key = word.casefold()
            if word and key not in seen:
                seen.add(key)
                words.append(word)
        return words

    @classmethod
    def _related_words(cls, entries: list[dict], key: str) -> list[str]:
        values = []
        for entry in entries:
            for meaning in entry.get("meanings") or []:
                values.extend(meaning.get(key) or [])
                for definition in meaning.get("definitions") or []:
                    values.extend(definition.get(key) or [])
        return cls._unique_words(values)

    @staticmethod
    def _pronunciation(entry: dict) -> tuple[Optional[str], Optional[str]]:
        phonetic = entry.get("phonetic")
        audio = None
        for item in entry.get("phonetics") or []:
            phonetic = phonetic or item.get("text")
            if item.get("audio"):
                audio = str(item["audio"])
                if audio.startswith("//"):
                    audio = f"https:{audio}"
                break
        return phonetic, audio

    @classmethod
    def _definition_embeds(cls, entries: list[dict]) -> list[discord.Embed]:
        embeds = []
        for entry in entries[:MAX_ENTRIES]:
            word = str(entry.get("word") or "Dictionary").strip()
            phonetic, audio = cls._pronunciation(entry)
            description_parts = []
            if phonetic:
                description_parts.append(f"Pronunciation: *{phonetic}*")
            if audio:
                description_parts.append(f"[Listen to pronunciation]({audio})")
            embed = discord.Embed(
                title=word,
                description=" · ".join(description_parts) or None,
                color=0x5865F2,
            )
            source_urls = entry.get("sourceUrls") or []
            if source_urls:
                embed.url = str(source_urls[0])

            for meaning in (entry.get("meanings") or [])[:MAX_MEANINGS]:
                lines = []
                for index, definition in enumerate((meaning.get("definitions") or [])[:MAX_DEFINITIONS], start=1):
                    text = str(definition.get("definition") or "").strip()
                    if not text:
                        continue
                    lines.append(f"**{index}.** {text}")
                    example = str(definition.get("example") or "").strip()
                    if example:
                        lines.append(f"*Example: {example}*")
                if lines:
                    value = "\n".join(lines)
                    embed.add_field(
                        name=str(meaning.get("partOfSpeech") or "Meaning").title(),
                        value=value[:1024],
                        inline=False,
                    )

            synonyms = cls._related_words([entry], "synonyms")[:12]
            antonyms = cls._related_words([entry], "antonyms")[:12]
            if synonyms:
                embed.add_field(name="Synonyms", value=", ".join(synonyms)[:1024], inline=False)
            if antonyms:
                embed.add_field(name="Antonyms", value=", ".join(antonyms)[:1024], inline=False)
            embed.set_footer(text="Definitions provided by Free Dictionary API")
            if embed.fields:
                embeds.append(embed)
        return embeds

    async def _send_definition(self, ctx: commands.Context, term: str) -> None:
        async with ctx.typing():
            entries, error = await self._lookup(term)
        if error:
            await ctx.send(error)
            return
        embeds = self._definition_embeds(entries or [])
        if not embeds:
            await ctx.send(f"I found **{term}**, but it did not include any definitions.")
            return
        for embed in embeds:
            await ctx.send(embed=embed)

    async def _send_related(self, ctx: commands.Context, term: str, relation: str) -> None:
        async with ctx.typing():
            entries, error = await self._lookup(term)
        if error:
            await ctx.send(error)
            return
        words = self._related_words(entries or [], relation)[:MAX_RELATED_WORDS]
        label = relation.title()
        if not words:
            await ctx.send(f"No {relation} were listed for **{term}**.")
            return
        embed = discord.Embed(
            title=f"{label} for {term}",
            description=", ".join(words)[:4096],
            color=0x5865F2,
            url=PROVIDER_URL,
        )
        embed.set_footer(text="Results provided by Free Dictionary API")
        await ctx.send(embed=embed)

    @staticmethod
    def _clean_urban_text(value: str) -> str:
        """Remove Urban Dictionary's bracket-link markup."""

        return re.sub(r"\[([^\]]+)]", r"\1", str(value or "")).strip()

    async def _send_urban(self, ctx: commands.Context, term: str) -> None:
        session = await self._get_session()
        try:
            async with ctx.typing():
                async with session.get(URBAN_API_URL, params={"term": term}) as response:
                    if response.status != 200:
                        await ctx.send(
                            f"Urban Dictionary returned HTTP {response.status}. Please try again later."
                        )
                        return
                    payload = await response.json(content_type=None)
        except aiohttp.ClientError:
            log.warning("Urban Dictionary API request failed for %r", term, exc_info=True)
            await ctx.send("I could not reach Urban Dictionary. Please try again later.")
            return
        except Exception:
            log.exception("Unexpected Urban Dictionary lookup failure for %r", term)
            await ctx.send("Something unexpected went wrong during that Urban Dictionary lookup.")
            return

        results = [item for item in payload.get("list", []) if isinstance(item, dict)]
        if not results:
            await ctx.send(f"Urban Dictionary does not have an entry for **{term}**.")
            return
        results.sort(
            key=lambda item: int(item.get("thumbs_up", 0) or 0) - int(item.get("thumbs_down", 0) or 0),
            reverse=True,
        )

        visible_results = results[:5]
        embeds = []
        for index, item in enumerate(visible_results, start=1):
            definition = self._clean_urban_text(item.get("definition"))
            example = self._clean_urban_text(item.get("example"))
            embed = discord.Embed(
                title=f"Urban Dictionary: {item.get('word') or term}",
                description=definition[:3800] or "No definition text.",
                color=0xEFFF00,
                url=str(item.get("permalink") or URBAN_PROVIDER_URL),
            )
            if example:
                embed.add_field(name="Example", value=example[:1024], inline=False)
            embed.add_field(
                name="Votes",
                value=(
                    f"👍 {int(item.get('thumbs_up', 0) or 0):,}   "
                    f"👎 {int(item.get('thumbs_down', 0) or 0):,}"
                ),
                inline=True,
            )
            embed.add_field(name="Author", value=str(item.get("author") or "Unknown")[:1024], inline=True)
            embed.add_field(
                name="Notice",
                value="Community-written definitions may be inaccurate or offensive.",
                inline=False,
            )
            embed.set_footer(text=f"Result {index} of {len(visible_results)} · Urban Dictionary")
            embeds.append(embed)

        view = UrbanDictionaryView(ctx.author.id, embeds)
        view.message = await ctx.send(embed=embeds[0], view=view)

    @commands.group(name="dictionary", aliases=["dict"], invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def dictionary(self, ctx: commands.Context, *, term: str = None):
        """Look up an English word or phrase."""

        if term is None:
            await ctx.send_help(ctx.command)
            return
        term = self._clean_term(term)
        if not term or len(term) > 100:
            await ctx.send("Enter a word or short phrase up to 100 characters.")
            return
        await self._send_definition(ctx, term)

    @commands.command(name="define")
    async def define(self, ctx: commands.Context, *, term: str):
        """Look up an English definition."""

        await self.dictionary.callback(self, ctx, term=term)

    @commands.command(name="synonym", aliases=["synonyms"])
    async def synonym(self, ctx: commands.Context, *, term: str):
        """Show synonyms for an English word."""

        term = self._clean_term(term)
        await self._send_related(ctx, term, "synonyms")

    @commands.command(name="antonym", aliases=["antonyms"])
    async def antonym(self, ctx: commands.Context, *, term: str):
        """Show antonyms for an English word."""

        term = self._clean_term(term)
        await self._send_related(ctx, term, "antonyms")

    @commands.command(name="urban", aliases=["urbandictionary", "ud"])
    async def urban_dictionary(self, ctx: commands.Context, *, term: str):
        """Look up community-written slang."""

        term = self._clean_term(term)
        if not term or len(term) > 100:
            await ctx.send("Enter a word or short phrase up to 100 characters.")
            return
        await self._send_urban(ctx, term)
