# Clanker

Clanker is a Discord-side helper for preparing token launch requests for the Clanker protocol on Base.

This first milestone deliberately keeps custody outside the cog:

- no private keys or seed phrases are stored;
- API tokens are stored through Red's shared API token storage;
- launches are dry-run/review-first by default;
- optional submit mode requires bot-owner configuration;
- the token creator/submitter is the primary reward recipient by default;
- the bot owner/platform takes a modest configurable creator-reward cut, defaulting to 10%;
- optional creator vault defaults can lock a configured percent of supply for the creator without replacing the bot-owner reward cut;
- each launch request is recorded in a bounded guild audit log.

## Setup

```text
[p]clankerset apiurl <api-base-url>
[p]clankerset apitoken <token>
[p]clankerset treasury <0x...>
[p]clankerset platformbps 1000
[p]clankerset vault enabled false
[p]clankerset enabled true
```

Keep submit mode disabled while validating the exact Clanker API contract:

```text
[p]clankerset submit false
```

When the endpoint and wallet/custody flow have been reviewed, a bot owner can enable API submission:

```text
[p]clankerset submit true
```

## User flow

```text
[p]clanker launch TICKER "Token Name" 1000000 0xCreatorAddress... optional image URL and description
```

The command validates basic metadata and the creator's primary reward address, builds a Base-chain launch payload, displays it for review, records the request, and only POSTs it to the configured API when submit mode is enabled. The payload uses Clanker v4-style `rewards.recipients` entries so the creator receives the majority reward split and the configured bot-owner treasury receives the platform cut. Optional vault settings allocate locked token supply to the creator; they do not replace the 10% bot-owner creator-reward split.

## Commands

- `[p]clanker launch <symbol> <name> <supply> <creator_address> [image_url] [description]` - create a launch request.
- `[p]clanker status` - show whether the cog is enabled and whether submit mode is active.
- `[p]clanker audit [limit]` - view recent launch request records.
- `[p]clankerset view` - show configuration without secrets.
- `[p]clankerset enabled <true|false>` - enable or disable the cog.
- `[p]clankerset submit <true|false>` - enable or disable live API submissions.
- `[p]clankerset apiurl <url>` - set the Clanker API base URL.
- `[p]clankerset apitoken <token>` - store the Clanker API bearer token.
- `[p]clankerset treasury <0x...>` - set the bot-owner/SickGaming platform treasury address.
- `[p]clankerset platformbps <0-10000>` - set the bot-owner reward cut, default 1000 bps / 10%.
- `[p]clankerset vault enabled <true|false>` - enable or disable creator vault allocation.
- `[p]clankerset vault percentage <0-90>` - set percent of total supply vaulted for the creator.
- `[p]clankerset vault lockup <seconds>` - set creator vault lockup; minimum 604800 seconds / 7 days.
- `[p]clankerset vault vesting <seconds>` - set optional creator vault vesting; 0 disables vesting.
- `[p]clankerset audit clear` - clear the guild audit log.
