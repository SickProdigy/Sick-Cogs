# ARK: Survival Ascended Announcements

Publish new official ARK: Survival Ascended Steam announcements in Discord. Version 1.0.0 focuses only on announcement delivery; it does not use RCON, query game servers, or require an API key.

## Setup

```text
[p]arkset channel #ark-updates
[p]arkset role @ARK Updates
[p]arkset autopost start 5
```

Setting the channel records the current Steam announcements so setup never floods the channel with old posts. Future matching announcements are delivered once, including across cog reloads and bot restarts.
Every official Steam announcement category is included by default. To receive fewer posts, set one or more
categories; for example, `[p]arkset categories updates events`, `[p]arkset categories community`, or
`[p]arkset categories updates`.

When starting automatic posting, optionally add a number from 2 to 10 to publish that many recent matching posts first,
oldest to newest: `[p]arkset autopost start 5`. Backfilled posts are spaced one second apart and do not mention the notification role. The backfill searches Steam's 100 most recent posts and respects the configured categories.

## Categories

- `updates` — posts whose titles identify an update, patch, upgrade, or version change
- `hotfixes` — only posts that explicitly say “hotfix”; this is not Steam’s “Regular Update” label
- `community` — Community Crunch posts
- `events` — in-game events and bonus-rate announcements
- `wipes` — wipe and transfer notices
- `releases` — maps, DLC, and content releases
- `promotions` — sales and promotional notices
- `all` — every official Steam announcement

The default is `all`. Servers can opt into a narrower selection with `[p]arkset categories`; uncertain
posts use the `official` classification and are delivered when `all` is selected.

These are content filters assigned by the bot from each post’s title and text. They are not Steam’s own
publication badges. Steam separately labels posts as Regular Update, Major Update, Patch Notes, and
In-Game Event; a Steam Regular Update is not necessarily a hotfix.

## Commands

```text
[p]ark
[p]ark latest [category]
[p]ark updates [2-10]  # newest first
[p]arkset status
[p]arkset channel [channel]
[p]arkset autopost start [recent-posts]
[p]arkset autopost stop
[p]arkset role [role]
[p]arkset categories [categories...]
[p]arkset interval <minutes>
[p]arkset preview [category]
[p]arkset check
```

Configuration commands require Manage Server or Red administrator access.
