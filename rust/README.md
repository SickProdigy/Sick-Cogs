# Rust

Publish new official Rust Steam announcements in Discord. It does not use RCON, query game servers, or require an API key.

## Setup

```text
[p]rustset channel #rust-updates
[p]rustset mode article
[p]rustset role @Rust Updates
[p]rustset autopost start 5
```

Setting the channel records the current Steam announcements so setup never floods the channel with old posts. Future matching announcements are delivered once, including across cog reloads and bot restarts.
Every official Steam announcement category is included by default. To receive fewer posts, set one or more
categories; for example, `[p]rustset categories updates events`, `[p]rustset categories devblogs`, or
`[p]rustset categories updates`.

When starting automatic posting, optionally add a number from 1 to 10 to publish that many recent matching posts first,
oldest to newest: `[p]rustset autopost start 5`. Backfilled posts are spaced one second apart and do not mention the notification role. The backfill searches Steam's 100 most recent posts and respects the configured categories.

Delivery defaults to `card`, a compact embed with the first full-size artwork from the Steam post; narrow title strips and dividers are skipped. `article` sends one regular Discord message containing the article text and as many as four suitable Steam images uploaded together as a gallery. Steam's trailer marker is replaced in place with the actual YouTube link. Automatic link embeds are suppressed so Steam and YouTube do not add separate preview cards beneath the post; the uploaded image gallery remains visible. Gallery images are resized to a maximum 1600-pixel edge and compressed before upload, with a 5 MB combined cap. Long articles end with a Continue reading link to Steam. Use `[p]rustset preview` to test the selected mode.

## Categories

- `updates` — posts whose titles identify an update, patch, upgrade, or version change, plus named posts explicitly describing this patch, a monthly update, or patch notes
- `hotfixes` — only posts that explicitly say “hotfix”; this is not Steam’s “Regular Update” label
- `devblogs` — official developer blogs and development recaps
- `events` — in-game events and bonus-rate announcements
- `releases` — maps, DLC, and content releases
- `promotions` — sales and promotional notices
- `all` — every official Steam announcement

The default is `all`. Servers can opt into a narrower selection with `[p]rustset categories`; uncertain
posts use the `official` classification and are delivered when `all` is selected.

These are content filters assigned by the bot from each post’s title and text. They are not Steam’s own
publication badges. Steam may separately label posts as Regular Update, Major Update, Patch Notes, and
In-Game Event; a Steam label is not used as the bot category.

## Scope and limitations

This version reads Facepunch's official Steam announcements for app 252490. Independent Facepunch website feeds, server queries, RCON, wipe scheduling, and GameRoles integration remain follow-ups in #213. No wipe date is inferred from a regular update. Cards show excerpts from the source, not generated gameplay summaries.

Polling defaults to ten minutes; one feed request serves all due guilds. Provider failures wait until the next configured interval. Stored IDs suppress repeats across ordinary reloads; an interruption between Discord accepting a message and saving its ID can still duplicate that message. Edited posts with the same ID are not republished. Only the latest 50 official items are checked automatically, so a sufficiently long outage may miss older posts.

## Updating or removing

A normal `[p]cog update` followed by `[p]reload rust` retains channels, roles, categories, and posted-item history. Use `[p]unload rust` to stop the cog while leaving it installed, or `[p]cog uninstall rust` to remove its installed code. Persistent Red data is retained unless it is deleted separately.

## Commands

```text
[p]rust
[p]rust latest [category]
[p]rust updates [position]  # newest is 1
[p]rust updates list [1-10]
[p]rustset status
[p]rustset channel [channel]
[p]rustset autopost start [recent-posts]
[p]rustset autopost stop
[p]rustset role [role]
[p]rustset mode [card|article]
[p]rustset categories [categories...]
[p]rustset interval <minutes>
[p]rustset preview [category]
[p]rustset check
```

Configuration commands require Manage Server or Red administrator access.
