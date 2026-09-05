# Clanker

Clanker is a Discord-side helper for preparing token launch requests for the Clanker protocol on Base.

This first milestone deliberately keeps custody outside the cog:

- no private keys or seed phrases are stored;
- API tokens are stored through Red's shared API token storage;
- launches are dry-run/review-first by default;
- optional submit mode requires bot-owner configuration;
- each launch request is recorded in a bounded guild audit log.

## Setup

```text
[p]clankerset apiurl <api-base-url>
[p]clankerset apitoken <token>
[p]clankerset treasury <0x...>
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
[p]clanker launch TICKER "Token Name" 1000000 0xBeneficiary... optional image URL and description
```

The command validates basic metadata and beneficiary addresses, builds a Base-chain launch payload, displays it for review, records the request, and only POSTs it to the configured API when submit mode is enabled.

## Commands

- `[p]clanker launch <symbol> <name> <supply> <beneficiary> [image_url] [description]` - create a launch request.
- `[p]clanker status` - show whether the cog is enabled and whether submit mode is active.
- `[p]clanker audit [limit]` - view recent launch request records.
- `[p]clankerset view` - show configuration without secrets.
- `[p]clankerset enabled <true|false>` - enable or disable the cog.
- `[p]clankerset submit <true|false>` - enable or disable live API submissions.
- `[p]clankerset apiurl <url>` - set the Clanker API base URL.
- `[p]clankerset apitoken <token>` - store the Clanker API bearer token.
- `[p]clankerset treasury <0x...>` - set the primary SickGaming beneficiary/treasury address.
- `[p]clankerset secondarybps <0-10000>` - set default secondary beneficiary basis points.
- `[p]clankerset audit clear` - clear the guild audit log.
