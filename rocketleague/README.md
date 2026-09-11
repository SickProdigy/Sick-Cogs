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
3. Optionally create a start.gg token under Developer Settings and configure it privately:

   ```text
   [p]set api startgg token,YOUR_TOKEN
   ```

4. Use `[p]rlcsset status` to inspect the cache and `[p]rlcs upcoming` to display it.

## Commands

- `[p]rlcs` or `[p]rl` - show upcoming RLCS events from the weekly BLAST cache.
- `[p]rlcs upcoming [1-10]` - show the same schedule with an optional result limit.
- `[p]rocketleague rlcs` - show the next event through the full command group.
- `[p]rocketleague rlcs upcoming [1-10]` - show later events through the full command group.
- `[p]rlcs event <start.gg URL or slug>` - optional detailed start.gg tournament lookup.
- `[p]rlcsset channel #channel` - enable automatic schedule updates for a server.
- `[p]rlcsset disable` - disable automatic updates.
- `[p]rlcsset status` - show the channel and global cache timestamps.
- `[p]rlcsset refresh` - owner-only refresh that enforces the seven-day minimum.
- `[p]rlcsset postnow` - post a cache-only preview without contacting BLAST.

Manual commands and restarts cannot bypass the BLAST limit. A failed request records the
attempt and retains the previous good cache. Announcements persist fingerprints per guild,
so unchanged events are not posted twice.

## Scope

Player rank/MMR lookup and the experimental PsyNet companion are deferred. Weekly data is
appropriate for schedules and upcoming-event reminders, not live scores or fast-changing
brackets. Those require a separately authorized higher-frequency source.
