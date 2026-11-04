# Gray Zone Warfare

Find Gray Zone Warfare quest pages and compact source-linked guidance without leaving Discord to search manually.

## Commands

```text
[p]grayzonewarfare quest <quest name or keywords>
[p]gzw quest <quest name or keywords>
[p]gzw task <quest name or keywords>
```

Exact titles open directly. Partial terms and common spelling variations use local ranking across the current quest catalog. When several quests are plausible, the requesting user receives a short selection menu. Every result keeps the full wiki page one click away.

## Source and limitations

The cog reads the public GZW Data API, which explicitly supports Discord bots and refreshes its quest dataset from the Gray Zone Warfare Fandom community wiki. Each result links to that source wiki page. Results display the source link and API data-snapshot date. Fields may include task giver, location, task type, objectives, faction-specific wording, and the sourced briefing when the dataset supplies them.

Gray Zone Warfare is updated frequently. Quest names, locations, faction wording, and objectives can change after game patches, and community wiki pages may lag behind. Treat the linked page and displayed data-snapshot date as the source of record. The cog reports unavailable or unparseable pages instead of generating missing steps.

No API key or inbound network access is required. The bot needs outbound HTTPS access to `gzw-data.dev`. Discord users open `gray-zone-warfare.fandom.com` only when they follow a result link.

## Caching and privacy

Search terms are matched locally against the cached GZW Data quest catalog. Results are cached only in memory for up to 10 minutes, with at most 128 cached searches, and are discarded when the cog unloads or the bot restarts. The cog does not persistently store user data or search history. Live requests are throttled globally and command use is rate-limited per user.

## Attribution

Quest records are supplied by the GZW Data API from Gray Zone Warfare Wiki community data. Gray Zone Warfare is developed by MADFINGER Games. This cog is unofficial and is not affiliated with MADFINGER Games or Fandom.
