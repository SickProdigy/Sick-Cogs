# RSSPublisher

Publish RSS and Atom feed updates to Discord channels with configurable formatting,
announcements, filtering, and delivery behavior.

## Setup

Install and load the cog, then add a feed from the channel where it should post:

```text
[p]rsspublisher add news #updates https://example.com/feed.xml
```

RSSPublisher makes outbound requests to administrator-configured feed URLs. Feed URLs,
channel destinations, templates, and delivery state are stored in Red's Config system.

## Common commands

```text
[p]rsspublisher list
[p]rsspublisher listall
[p]rsspublisher view news #updates
[p]rsspublisher force news
[p]rsspublisher template news #updates <template>
[p]rsspublisher announce news #updates <announcement>
[p]rsspublisher filter titleprefix news #updates News -
[p]rsspublisher filter author allow news #updates xSicKxBot
[p]rsspublisher filter show news #updates
[p]rsspublisher pause news #updates
[p]rsspublisher resume news #updates
[p]rsspublisher remove news #updates
```

`[p]rss` is an alias for `[p]rsspublisher`. Replace `[p]` with the bot's configured
prefix. The root help keeps every subcommand visible with a compact description; use `[p]help rss <command>` for complete syntax and details.

## Inspecting feeds

`[p]rss list [channel]` shows every feed in one channel. `[p]rss listall` shows the
same compact settings for every server channel. Both include the source URL, effective
delivery style, active/paused state, latest/catch-up mode, and whether an announcement
is configured.

Use `[p]rss view <feed> [channel]` for one complete settings and health card. `info`
and `settings` are aliases. Existing `showtemplate` and `status` commands remain
available as focused compatibility views.

To let Discord build its native preview from a site's Open Graph metadata, use a
link-only template and disable RSSPublisher's custom embed:

```text
[p]rss template news #updates $link
[p]rss embed toggle news #updates
```

These feeds are labeled **Native link preview** in `list`, `listall`, and `view`.

## Entry filters

Feeds are unrestricted by default. Administrators can require a case-insensitive title prefix, exact normalized author names, and/or the existing tag allowlist. Every configured filter family must pass. Author markup from feeds is reduced to plaintext before exact matching.

Use `clear` with `filter titleprefix` to remove the title restriction, and `filter author remove` to remove an author. Automatic checks intentionally advance past rejected entries so spam is not reconsidered every cycle; `rss force` reports a rejection without advancing the marker. Active filters appear in `list`, `listall`, `view`, and `filter show`.

Role mentions in announcements are limited to roles both the configuring moderator and
the bot are permitted to mention. User and everyone mentions from feed content are
suppressed.

## Replacing legacy RSS

RSSPublisher 3.0.1 uses a new Sick-Cogs Config namespace. On first initialization it imports legacy RSS channel feeds and their delivery markers from identifier `2761331001` before scheduling any feed checks. The legacy data remains unchanged.

After updating and reloading RSSPublisher, a bot owner can verify the one-time import with:

```text
[p]rss migrationstatus
```

Do not clear legacy Red data during replacement. Confirm retained feeds with `[p]rss listall`; existing last-entry markers are retained so already delivered entries are not replayed.
