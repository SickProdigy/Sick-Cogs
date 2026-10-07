# Icarus Announcements

Publish new official Icarus Steam announcements in Discord. It does not use RCON, query game servers, or require an API key.

## Setup

```text
[p]icarusset channel #icarus-updates
[p]icarusset mode article
[p]icarusset role @Icarus Updates
[p]icarusset autopost start 5
```

Setting the channel records the current Steam announcements so setup never floods the channel with old posts. Future matching announcements are delivered once, including across cog reloads and bot restarts.
Every official Steam announcement category is included by default. To receive fewer posts, set one or more
categories; for example, `[p]icarusset categories updates events`, `[p]icarusset categories devblogs`, or
`[p]icarusset categories updates`.

When starting automatic posting, optionally add a number from 2 to 10 to publish that many recent matching posts first,
oldest to newest: `[p]icarusset autopost start 5`. Backfilled posts are spaced one second apart and do not mention the notification role. The backfill searches Steam's 100 most recent posts and respects the configured categories.

Delivery defaults to `card`, a compact embed with the first full-size artwork from the Steam post; narrow title strips and dividers are skipped. `article` sends one regular Discord message containing the article text and as many as four suitable Steam images uploaded together as a gallery. Steam's trailer marker is replaced in place with the actual YouTube link. Automatic link embeds are suppressed so Steam and YouTube do not add separate preview cards beneath the post; the uploaded image gallery remains visible. Gallery images are resized to a maximum 1600-pixel edge and compressed before upload, with a 5 MB combined cap. Long articles end with a Continue reading link to Steam. Use `[p]icarusset preview` to test the selected mode.

## Categories

- `updates` — posts whose titles identify an update, patch, upgrade, or version change
- `hotfixes` — only posts that explicitly say “hotfix”; this is not Steam’s “Regular Update” label
- `devblogs` — official developer blogs and development recaps
- `events` — in-game events and bonus-rate announcements
- `releases` — maps, DLC, and content releases
- `promotions` — sales and promotional notices
- `all` — every official Steam announcement

The default is `all`. Servers can opt into a narrower selection with `[p]icarusset categories`; uncertain
posts use the `official` classification and are delivered when `all` is selected.

These are content filters assigned by the bot from each post’s title and text. They are not Steam’s own
publication badges. Steam may separately label posts as Regular Update, Major Update, Patch Notes, and
In-Game Event; a Steam label is not used as the bot category.

## Commands

```text
[p]icarus
[p]icarus latest [category]
[p]icarus updates [2-10]  # newest first
[p]icarusset status
[p]icarusset channel [channel]
[p]icarusset autopost start [recent-posts]
[p]icarusset autopost stop
[p]icarusset role [role]
[p]icarusset mode [card|article]
[p]icarusset categories [categories...]
[p]icarusset interval <minutes>
[p]icarusset preview [category]
[p]icarusset check
```

Configuration commands require Manage Server or Red administrator access.
