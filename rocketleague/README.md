# RocketLeague

RocketLeague is a fresh, supported-data replacement for the legacy RLStats experiment.
Its initial scope is Rocket League Championship Series tournament discovery through
the documented start.gg GraphQL API. It does not scrape Tracker Network and does not
claim to provide player ranks or MMR.

## Setup

1. Sign in to start.gg and create a token under Developer Settings.
2. Copy the token when it is displayed; start.gg tokens expire after one year.
3. As the bot owner, configure it in a DM or other private channel:

   ```text
   [p]set api startgg token,YOUR_TOKEN
   ```

4. Load the cog and try `[p]rlcs upcoming`.

## Commands

- `[p]rlcs upcoming [1-10]` — show upcoming RLCS tournaments listed by start.gg.
- `[p]rlcs event <start.gg URL or slug>` — show events within a Rocket League tournament.

start.gg is currently limited to an average of 80 API requests per 60 seconds. The
cog uses small queries and does not poll in this initial release.

## Scope

Player rank/MMR lookup is intentionally excluded because no supported public remote
Rocket League profile API has been identified. Scheduled RLCS channel announcements,
results, and standings can be added after live start.gg coverage is validated.
