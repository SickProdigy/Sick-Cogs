# ARK: Survival Ascended

Publish new official ARK: Survival Ascended Steam announcements in Discord. It does not use RCON, query game servers, or require an API key.

## Setup

```text
[p]arkset channel #ark-updates
[p]arkset mode article
[p]arkset role @ARK Updates
[p]arkset autopost start 5
```

Setting the channel records the current Steam announcements so setup never floods the channel with old posts. Future matching announcements are delivered once, including across cog reloads and bot restarts.
Every official Steam announcement category is included by default. To receive fewer posts, set one or more
categories; for example, `[p]arkset categories updates events`, `[p]arkset categories community`, or
`[p]arkset categories updates`.

When starting automatic posting, optionally add a number from 2 to 10 to publish that many recent matching posts first,
oldest to newest: `[p]arkset autopost start 5`. Backfilled posts are spaced one second apart and do not mention the notification role. The backfill searches Steam's 100 most recent posts and respects the configured categories.

Delivery defaults to `card`, a compact embed with the first full-size artwork from the Steam post; narrow title strips and dividers are skipped. `article` sends one regular Discord message containing the article text and as many as four suitable Steam images uploaded together as a gallery. Steam's trailer marker is replaced in place with the actual YouTube link. Automatic link embeds are suppressed so Steam and YouTube do not add separate preview cards beneath the post; the uploaded image gallery remains visible. Gallery images are resized to a maximum 1600-pixel edge and compressed before upload, with a 5 MB combined cap. Long articles end with a Continue reading link to Steam. Use `[p]arkset preview` to test the selected mode.

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

## Updating or removing

Version 1.1.3 changes Red's displayed cog name from `ArkAnnouncements` to `Ark` without changing the `ark` install package, commands, or saved configuration. A normal `[p]cog update` followed by `[p]reload ark` keeps automatic posting enabled and retains channels, roles, categories, and posted-item history. Use `[p]unload ark` to stop the cog while leaving it installed, or `[p]cog uninstall ark` to remove its installed code. Persistent Red data is intentionally retained unless it is deleted separately.

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
[p]arkset mode [card|article]
[p]arkset categories [categories...]
[p]arkset interval <minutes>
[p]arkset preview [category]
[p]arkset check
```

Configuration commands require Manage Server or Red administrator access.
