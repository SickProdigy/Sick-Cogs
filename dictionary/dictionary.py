import logging
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
from redbot.core import commands


log = logging.getLogger("red.sick-cogs.Dictionary")

API_BASE = "https://api.dictionaryapi.dev/api/v2/entries/en"
PROVIDER_URL = "https://dictionaryapi.dev/"
USER_AGENT = "Sick-Cogs-Dictionary/2.1.0 (+https://github.com/SickProdigy/Sick-Cogs)"
MAX_ENTRIES = 3
MAX_MEANINGS = 4
MAX_DEFINITIONS = 3
MAX_RELATED_WORDS = 25


class Dictionary(commands.Cog):
    """Look up English definitions and related words."""

    __author__ = ["SickProdigy"]
    __version__ = "2.1.0"

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

    @commands.group(name="dictionary", aliases=["dict", "define"], invoke_without_command=True)
    @commands.bot_has_permissions(embed_links=True)
    async def dictionary(self, ctx: commands.Context, *, term: str = None):
        """Look up an English word or phrase.

        Related commands:
        - `[p]define <word or phrase>` looks up a definition.
        - `[p]syn <word>` is the short form of `[p]synonym`.
        - `[p]ant <word>` is the short form of `[p]antonym`.
        - `[p]urban <term>` is provided by Red's General cog.
        """

        if term is None:
            await ctx.send_help(ctx.command)
            return
        term = self._clean_term(term)
        if not term or len(term) > 100:
            await ctx.send("Enter a word or short phrase up to 100 characters.")
            return
        await self._send_definition(ctx, term)

    @commands.command(name="synonym", aliases=["synonyms", "syn"])
    async def synonym(self, ctx: commands.Context, *, term: str):
        """Show synonyms for an English word."""

        term = self._clean_term(term)
        await self._send_related(ctx, term, "synonyms")

    @commands.command(name="antonym", aliases=["antonyms", "ant"])
    async def antonym(self, ctx: commands.Context, *, term: str):
        """Show antonyms for an English word."""

        term = self._clean_term(term)
        await self._send_related(ctx, term, "antonyms")
