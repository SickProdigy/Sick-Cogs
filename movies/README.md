# Movie Releases

Post new movie release announcements to a configured Discord channel using TMDb data.

Run `[p]movies` for search examples and a few random suggestions. Look up a movie by title with `[p]movies <title>` (or its `[p]movie` alias), for example
`[p]movies interstellar` or `[p]movie dune 1984`. The result includes its poster,
synopsis, release date,
runtime, genres, status, and TMDb rating.

## Setup

1. Get a TMDb API key from <https://www.themoviedb.org/settings/api>.
2. As the bot owner, set the shared key in a DM or private channel with `[p]set api tmdb api_key,YOUR_KEY`.
3. Choose where announcements should appear with `[p]movieset channel #movies`.
4. Optional: let users opt in to notifications by assigning them a role, then set `[p]movieset role @MovieNews`.
5. Start scheduled checks with `[p]movieset enable`; stop them with `[p]movieset disable`.

The cog checks hourly, posts only unposted movie IDs, and enforces a configurable per-day cap so the channel is not spammed.

## Commands

- `[p]movies [title]` - show search guidance and random suggestions, or look up the supplied title.
- `[p]movie <title>` - alias for `[p]movies <title>`.
- `[p]movieset channel [channel]` - set the announcement channel, defaulting to the current channel.
- `[p]movieset role <role>` - set the optional role mentioned by automatic release posts.
- `[p]movieset roleclear` - stop mentioning the notification role.
- `[p]movieset enable` - start hourly automatic release posts.
- `[p]movieset disable` - stop automatic posts; lookups remain available.
- `[p]movieset maxperday <1-25>` - cap automatic posts per day.
- `[p]movieset window <days_back> <days_ahead>` - configure the release-date window.
- `[p]movieset minvotes <count>` - avoid very low-signal releases.
- `[p]movieset preview` - list matching releases without posting.
- `[p]movieset force` - post the next unposted release immediately.
- `[p]movieset settings` - show current settings.
- `[p]movieset clearhistory` - allow previously-posted movie IDs to post again.
