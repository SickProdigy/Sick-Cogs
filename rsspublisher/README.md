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
