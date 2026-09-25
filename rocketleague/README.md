# RocketLeague

RocketLeague posts Rocket League Championship Series schedule updates. It makes at most
one permitted request to the public BLAST tournament catalog every seven days, stores a
server-side snapshot in Red Config, and serves commands and guild announcements from
that cache. The documented start.gg GraphQL API remains an optional supplemental source.

## Setup

1. Choose a server announcement channel:

   ```text
   [p]rlcsset channel #rlcs-news
   ```

2. The scheduler fetches BLAST when the seven-day window permits it and announces newly
   discovered or materially changed events beginning within 45 days.
3. Configure provider credentials privately when using supplemental tournaments:

   ```text
   [p]set api startgg token,YOUR_STARTGG_TOKEN
   [p]set api challonge client_id,YOUR_CLIENT_ID client_secret,YOUR_CLIENT_SECRET
   ```

   Challonge uses its OAuth client-credentials flow. No callback URL is needed because the
   bot acts only as the registered server-side application.

4. Use `[p]rlcsset status` to inspect the cache and `[p]rlcs upcoming` to display it.

## Commands

`[p]rocketleague` is the canonical user command group. Running it without a
subcommand displays a focused user card. `[p]help rocketleague` provides the complete
reference, including the separate `[p]rlcsset` administrator commands:

- `[p]rocketleague rlcs` - show the next event from the weekly BLAST cache.
- `[p]rocketleague rlcs upcoming [1-10]` - show later scheduled events.
- `[p]rocketleague rlcs event <start.gg URL or slug>` - optional detailed
  start.gg tournament lookup.

`[p]rl` is an alias for the complete `[p]rocketleague` group, so commands such as
`[p]rl tourney` and `[p]rl rlcs` follow the canonical structure. `[p]rlcs` remains a
direct shortcut to the official schedule; `[p]rlcs upcoming` and `[p]rlcs event <URL or
slug>` remain available.

Tournament URLs are configured independently for each Discord server. Administrators do
not need to choose a provider-specific command; the cog identifies the provider from the
URL:

- `[p]rocketleagueset tournamentadd <url>` - validate and save a tournament URL.
- `[p]rocketleagueset tournamentremove <id>` - remove a saved tournament by its short server ID.
- `[p]rocketleagueset tournamentrefresh [id]` - explicitly refresh one or all cached tournament records.
- `[p]rocketleagueset tournaments` - list the server’s saved tournament URLs.
- `[p]rocketleague tournaments` or `tourney`/`tourneys` - show upcoming saved tournaments.
- `[p]rocketleague tournaments recent` - show recently completed saved tournaments.

Direct start.gg and Challonge tournament URLs are supported. start.gg uses a shared
developer token; Challonge uses a shared OAuth client ID and client secret to obtain a
short-lived API v2.1 access token in memory. Adding a URL validates it and caches its public
details, including available format, entrant counts, capacity, registration status and closing time, location,
and prize information. start.gg cards also preserve tournament and per-event lifecycle state, phase schedules, team sizes, direct phase links, and the last successful refresh time without guessing missing provider fields. Normal tournament views read only the cache and make no provider
requests. Active records are refreshed automatically about once per day and duplicate provider
URLs shared across servers are fetched only once per cycle. Failed refreshes retain the previous
good cache. Completed tournaments stop refreshing.

The Challonge sandbox budget is capped at seven automatic tournament refreshes per UTC day;
each rich refresh can use one tournament request and one participant-count request. Manual
`tournamentrefresh` remains available when an organizer changes a date or status. Each server
can save up to 25 tournament URLs.

## Automatic community clips

Each Discord server can follow a mix of creator feeds and general Rocket League feeds.
The cog rotates between sources, avoids reposting remembered clips, and posts the provider's
canonical URL so Discord can render its native player. It never downloads or re-uploads video.
Provider results are cached for 24 hours, while the posting interval defaults to about 12 hours
with timing jitter. The default maximum clip length is 180 seconds.

Configure a channel and add sources:

```text
[p]rocketleagueset clips channel #rocket-league-clips
[p]rocketleagueset clips sourceadd https://medal.tv/games/rocket-league
[p]rocketleagueset clips sourceadd https://medal.tv/u/creator
[p]rocketleagueset clips sourceadd twitch creator_name
[p]rocketleagueset clips sourceadd https://www.youtube.com/@creator
[p]rocketleagueset clips sourceadd https://www.youtube.com/playlist?list=PLAYLIST_ID
[p]rocketleagueset clips sources
[p]rocketleagueset clips enable
```

A recognized Medal, Twitch, or YouTube URL selects its provider automatically. For a bare
creator name, put `medal`, `twitch`, or `youtube` before the name. A Medal clip URL follows
that clip's creator; the Rocket League game URL follows Medal's general discovery feed.
YouTube channels are filtered by duration because YouTube does not expose a dependable
Shorts-only API flag; a dedicated clips playlist provides the most precise source.

Twitch reuses the same Red shared API credentials as the core Streams cog:

```text
[p]set api twitch client_id,YOUR_ID client_secret,YOUR_SECRET
```

YouTube uses Red's shared `youtube` API-key namespace (also recognized by core Streams):

```text
[p]set api youtube api_key,YOUR_API_KEY
```

Medal uses a developer API key stored in Red.s shared token namespace:

```text
[p]set api medal api_key,YOUR_API_KEY
```

Useful controls:

- `[p]rocketleagueset clips sourceremove <id>` - remove a source listed by `sources`.
- `[p]rocketleagueset clips interval <hours>` - set an approximate 1-168 hour interval.
- `[p]rocketleagueset clips maxlength <seconds>` - allow clips from 15-600 seconds.
- `[p]rocketleagueset clips refresh` - refresh every configured source now.
- `[p]rocketleagueset clips postnow` - post one unseen eligible clip for testing.
- `[p]rocketleagueset clips status` - display the current server settings.
- `[p]rocketleagueset clips disable` - pause posting without deleting configuration.

The separate `[p]rlcsset` group is server-administrator configuration:

- `[p]rlcsset channel #channel` - enable automatic schedule updates for a server.
- `[p]rlcsset disable` - disable automatic updates.
- `[p]rlcsset status` - show the channel and global cache timestamps.
- `[p]rlcsset postnow` - post a cache-only preview without contacting BLAST.
- `[p]rlcsset refresh` - bot-owner-only refresh that enforces the seven-day minimum.

Manual commands and restarts cannot bypass the BLAST limit. A failed request records the
attempt and retains the previous good cache. Announcements persist fingerprints per guild,
so unchanged events are not posted twice.

## Scope

Player rank/MMR lookup and the experimental PsyNet companion are deferred. Weekly data is
appropriate for schedules and upcoming-event reminders, not live scores or fast-changing
brackets. Those require a separately authorized higher-frequency source.
