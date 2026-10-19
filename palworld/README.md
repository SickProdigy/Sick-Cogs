# Palworld

Publish official Pocketpair Palworld announcements from Steam to Discord. The cog does not query game servers, use RCON, or require an API key.

## Source behavior

Palworld is Steam App `1623730`. Steam's public news API mixes the app's own posts with third-party articles, so this cog accepts only `steam_community_announcements` records and ignores feeds such as PC Gamer and PCGamesN. Official posts currently use both `pocketpair_dev` and `pocketpair_admin`; the publisher-controlled feed is therefore more reliable than a single author name.

Steam supplies stable `gid` identifiers, timestamps, full BBCode content, original links, and artwork. A second source is not currently needed for the requested update, maintenance, event, and release announcements. Posts not published to Palworld's official Steam announcements will not be discovered.

## Setup

```text
[p]palworldset channel #palworld-updates
[p]palworldset mode card
[p]palworldset role @Palworld Updates
[p]palworldset autopost start 1
```

Setting the channel seeds the current official Steam IDs without posting them. Future matching announcements publish once, oldest to newest, across reloads and restarts. `autopost start` may optionally publish 1–10 recent matching posts without mentioning the notification role.

Delivery defaults to `card`, a compact embed with suitable Steam artwork. `article` posts longer text with up to four resized Steam images. External text cannot mention users or everyone.

## Categories

- `updates` — versions, patches, balance adjustments, and bug fixes
- `hotfixes` — posts explicitly identified as hotfixes
- `maintenance` — maintenance, downtime, and server-restart notices
- `events` — raids, campaigns, festivals, and other in-game events
- `releases` — content releases and collaborations
- `promotions` — sales, discounts, free weekends, and bundles
- `community` — polls, community features, fan art, and award posts
- `all` — every official Palworld Steam announcement

Uncertain posts receive the `official` classification and are delivered only when `all` is enabled. Categories are derived conservatively from official post titles and text; they are not Steam publication badges.

## Commands

```text
[p]palworld
[p]palworld latest [category]
[p]palworld updates [position]       # newest is 1
[p]palworld updates list [1-10]
[p]palworldset status
[p]palworldset channel [channel]
[p]palworldset autopost start [1-10]
[p]palworldset autopost stop
[p]palworldset role [role]
[p]palworldset mode [card|article]
[p]palworldset categories [categories...]
[p]palworldset interval <5-1440>
[p]palworldset preview [category]
[p]palworldset check
```

Configuration commands require Manage Server or Red administrator access.
