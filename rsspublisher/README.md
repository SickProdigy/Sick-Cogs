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
[p]rsspublisher force news
[p]rsspublisher template news #updates <template>
[p]rsspublisher announce news #updates <announcement>
[p]rsspublisher pause news #updates
[p]rsspublisher resume news #updates
[p]rsspublisher remove news #updates
```

`[p]rss` is an alias for `[p]rsspublisher`. Replace `[p]` with the bot's configured
prefix. Use `[p]help rsspublisher` and its subcommands for the full syntax.

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
