# Gray Zone Warfare

Find Gray Zone Warfare quests, actionable walkthrough steps, and source images without leaving Discord to search manually.

## Commands

```text
[p]grayzonewarfare quest <quest name or keywords>
[p]gzw quest <quest name or keywords>
[p]gzw task <quest name or keywords>
```

Exact titles open directly. Partial terms and common spelling variations use local ranking across the current quest catalog. When several quests are plausible, the requesting user receives a short selection menu. After matching the task, the cog fetches that page's structured Guide section through Fandom's MediaWiki API. Results prioritize the mission check and actionable completion steps, can display up to four sourced guide images, and keep the full wiki page one click away.

## Source and limitations

The cog reads the public GZW Data API for task discovery, then uses the selected Fandom wiki's supported MediaWiki API to request only that page's Guide section. Each result links to the source page and displays its API data-snapshot date. Fields may include task giver, location, task type, objectives, faction-specific wording, walkthrough steps, and up to four guide images. If a page has no structured Guide section or guide enrichment is unavailable, the cog keeps the sourced mission summary and direct page link instead of inventing steps.

Gray Zone Warfare is updated frequently. Quest names, locations, faction wording, and objectives can change after game patches, and community wiki pages may lag behind. Treat the linked page and displayed data-snapshot date as the source of record. The cog reports unavailable or unparseable pages instead of generating missing steps.

No API key or inbound network access is required. The bot needs outbound HTTPS access to `gzw-data.dev` and `gray-zone-warfare.fandom.com`. It does not scrape rendered pages or require a Fandom login; guide enrichment uses the public MediaWiki API.

## Caching and privacy

Search terms are matched locally against the cached GZW Data quest catalog. Task results and selected guide sections are cached only in memory for up to 10 minutes, with at most 128 cached searches, and are discarded when the cog unloads or the bot restarts. The cog does not persistently store user data or search history. Live requests are throttled globally and command use is rate-limited per user.

## Attribution

Quest records are supplied by the GZW Data API from Gray Zone Warfare Wiki community data. Gray Zone Warfare is developed by MADFINGER Games. This cog is unofficial and is not affiliated with MADFINGER Games or Fandom.
