# Gray Zone Warfare

Find Gray Zone Warfare quests, actionable walkthrough steps, and source images without leaving Discord to search manually.

## Commands

```text
[p]grayzonewarfare quest <quest name or keywords>
[p]gzw quest <quest name or keywords>
[p]gzw task <quest name or keywords>
```

Exact titles open directly. Partial terms and common spelling variations use local ranking across the current quest catalog. Every non-exact search shows up to five possible matches in a requester-only selection menu, including task giver and location. No walkthrough is displayed until the player chooses a quest, even when only one candidate is found. Common connecting words alone do not qualify a match. If none is right, use the provided full wiki search link; the source catalog may be missing a quest. After matching the task, the cog fetches that page's structured Guide section (or the article body for guide-only pages without sections) through Fandom's MediaWiki API. Results prioritize the mission check and actionable completion steps, can display up to five sourced guide images, and keep the full wiki page one click away.

## Source and limitations

The cog combines the public GZW Data API’s `tasks`, `contract`, and `task_guides` datasets for discovery, deduplicating normalized titles and preferring structured task fields. This API is a third-party import of the community wiki, rather than a direct complete wiki index. It then uses the selected Fandom wiki's supported MediaWiki API to request that page's Guide section, or its article body when the provider explicitly classifies it as a guide-only page without sections. Each result links to the source page and displays its API data-snapshot date. Fields may include task giver, location, task type, objectives, faction-specific wording, walkthrough steps, and up to five guide images. Raw guide-dataset text is not rendered as instructions because it can contain unprocessed wiki markup. If a page has no structured Guide section or guide enrichment is unavailable, the cog keeps the sourced mission summary and direct page link instead of inventing steps.

Gray Zone Warfare is updated frequently. Quest names, locations, faction wording, and objectives can change after game patches, and community wiki pages may lag behind. Treat the linked page and displayed data-snapshot date as the source of record. The cog reports unavailable or unparseable pages instead of generating missing steps.

No API key or inbound network access is required. The bot needs outbound HTTPS access to `gzw-data.dev` and `gray-zone-warfare.fandom.com`. It does not scrape rendered pages or require a Fandom login; guide enrichment uses the public MediaWiki API.

## Caching and privacy

Search terms are matched locally against the cached GZW Data quest catalog. Task results and selected guide sections are cached only in memory for up to 10 minutes, with at most 128 cached searches, and are discarded when the cog unloads or the bot restarts. The cog does not persistently store user data or search history. Live requests are throttled globally and command use is rate-limited per user.

## Attribution

Quest records are supplied by the GZW Data API from Gray Zone Warfare Wiki community data. Gray Zone Warfare is developed by MADFINGER Games. This cog is unofficial and is not affiliated with MADFINGER Games or Fandom.

## Official Steam announcements

Optional automatic posting uses Steam directly, independently of the third-party quest API. Server administrators can configure:

```text
[p]gzw updates
[p]gzwset channel #updates
[p]gzwset autopost start
[p]gzwset autopost stop
[p]gzwset interval 10
[p]gzwset
```

Only official Steam community announcements are accepted. Polling defaults to every 10 minutes (configurable from 5 to 1440). Setting a channel or starting marks existing posts as seen, preventing an old-post flood. Omit the channel to stop and clear it. Delivery uses an embed with the Steam article link, excerpt, and artwork when available; no roles or members are mentioned. Send Messages and Embed Links permissions are required.

Server settings, up to 500 posted item IDs, and polling timestamps persist across restarts. No member data is stored. Failed sends remain pending for a later check; as with the sibling Steam cogs, a crash between sending and saving its ID can cause a duplicate. The bounded latest-50 feed cannot guarantee recovery of every post after extended downtime. Steam API access requires outbound HTTPS to `api.steampowered.com`.
