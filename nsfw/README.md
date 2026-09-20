# Nsfw

Nsfw sends random mature images or GIFs in Discord channels marked NSFW.

Most commands fetch media from curated subreddit lists through the default Martine API source. The bot owner can switch to direct Reddit API requests, but that path may be rate limited more quickly.

Every media command is restricted by Red's NSFW-channel check. Provider failures, timeouts, malformed responses, and empty results return a generic error and never expose response details in Discord.

## Commands

- `[p]nsfwversion` - Show the installed cog version.
- `[p]cleandm <number>` - Delete recent bot messages from your direct message channel.
- `[p]nsfwset autopost` - Open the scheduled-feed help.
- `[p]nsfwset autopost [channel] [source] [minutes]` - Configure or replace the server's one scheduled feed. Supply at least one argument; the channel otherwise defaults to the current channel, the source to `random`, and the interval to 360 minutes (minimum 30). Random posts show the exact category command members can copy and run.
- `[p]nsfwset autopost status` - Show the configured feed, schedule, and any destination/provider problem.
- `[p]nsfwset autopost preview` - Preview one post in the current NSFW channel without changing the schedule.
- `[p]nsfwset autopost off` - Remove the server's scheduled feed.
- `[p]nsfwset switchredditapi` - Bot owner only. Toggle between the default Martine API source and direct Reddit API requests.

The cog also provides many category commands such as `[p]hentai`, `[p]porngif`, `[p]gonewild`, and other NSFW-only media commands. Use `[p]help Nsfw` in Discord for the full command list.

This cog stores its global API-source setting, one scheduled feed per server, and a bounded list of recently posted media URLs for duplicate suppression. It does not store user data. When using external media APIs, request metadata and selected categories may be processed by those services.
