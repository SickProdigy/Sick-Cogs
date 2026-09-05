# Clanker

Clanker is a Discord-side helper for preparing token launch requests for the Clanker protocol on Base.

This first milestone deliberately keeps custody outside the cog:

- no private keys or seed phrases are stored;
- API tokens are stored through Red's shared API token storage;
- launches are dry-run/review-first by default;
- optional submit mode requires bot-owner configuration;
- the token creator/submitter is the primary reward recipient by default;
- the bot owner/platform takes a configurable creator-reward cut, defaulting to 20%;
- optional airdrop defaults can reserve token supply for a prepared Merkle airdrop list;
- each launch request is recorded in a bounded guild audit log.

## Setup

```text
[p]clankerset apiurl <api-base-url>
[p]clankerset apitoken <token>
[p]clankerset treasury <0x...>
[p]clankerset platformbps 2000
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

The command validates basic metadata and the creator's primary reward address, builds a Base-chain launch payload, displays it for review, records the request, and only POSTs it to the configured API when submit mode is enabled. The payload uses Clanker v4-style `rewards.recipients` entries so the creator receives the majority reward split and the configured bot-owner treasury receives the platform cut. The default split is 80% creator / 20% bot-owner treasury.

Airdrops are optional and separate from creator rewards. Clanker v4 airdrops use a Merkle root, so the recipient list and proofs must be prepared outside the cog first. When enabled, the cog includes the configured `airdrop` object with total amount, Merkle root, lockup, optional vesting, and optional admin.

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
- `[p]clankerset platformbps <0-10000>` - set the bot-owner reward cut, default 2000 bps / 20%.
- `[p]clankerset airdrop enabled <true|false>` - enable or disable configured airdrop payloads.
- `[p]clankerset airdrop root <0x...>` - set the 32-byte Merkle root for the prepared airdrop list.
- `[p]clankerset airdrop amount <tokens>` - set total token amount reserved for the airdrop.
- `[p]clankerset airdrop lockup <seconds>` - set airdrop lockup; minimum 86400 seconds / 1 day.
- `[p]clankerset airdrop vesting <seconds>` - set optional airdrop vesting; 0 disables vesting.
- `[p]clankerset airdrop admin [0x...]` - set or clear the optional airdrop admin address.
- `[p]clankerset audit clear` - clear the guild audit log.
