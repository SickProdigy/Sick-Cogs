# Movie Releases

Post new movie release announcements to a configured Discord channel using TMDb data.

## Setup

1. Get a TMDb API key from <https://www.themoviedb.org/settings/api>.
2. Set the key with `[p]movieset apikey <key>`.
3. Choose where announcements should appear with `[p]movieset channel #movies`.
4. Optional: let users opt in to notifications by assigning them a role, then set `[p]movieset role @MovieNews`.
5. Enable scheduled checks with `[p]movieset enabled true`.

The cog checks hourly, posts only unposted movie IDs, and enforces a configurable per-day cap so the channel is not spammed.

## Commands

- `[p]movieset apikey <key>` - save the TMDb API key.
- `[p]movieset channel [channel]` - set the release announcement channel.
- `[p]movieset role [role]` - set or clear an opt-in mention role.
- `[p]movieset enabled <true|false>` - enable or disable scheduled posts.
- `[p]movieset maxperday <1-25>` - cap automatic posts per day.
- `[p]movieset window <days_back> <days_ahead>` - configure the release-date window.
- `[p]movieset minvotes <count>` - avoid very low-signal releases.
- `[p]movieset preview` - list matching releases without posting.
- `[p]movieset force` - post the next unposted release immediately.
- `[p]movieset settings` - show current settings.
- `[p]movieset clearhistory` - allow previously-posted movie IDs to post again.
