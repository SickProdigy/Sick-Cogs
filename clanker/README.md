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

The recommended flow is the interactive Discord launch card:

```text
[p]clanker card
```

The card lets the requester fill token basics in Discord modals, add optional details, add or clear an optional airdrop, preview the payload, and click **Submit Launch**. The card is bound to the user who opened it so other members cannot edit or submit that draft.

Airdrop entries can be entered as fixed token amounts or percentages of the draft supply:

```text
0x1111111111111111111111111111111111111111=1%
0x2222222222222222222222222222222222222222=250000000
0x3333333333333333333333333333333333333333 0.25%
```

Percentages are converted to whole token amounts using the draft supply, the total airdrop allocation is capped at 90% of supply to match Clanker extension limits, and lockup is enforced at Clanker's documented 7-day minimum. Recipient-list airdrops are preview-only until a matching Merkle root and proofs are generated. If a prebuilt Merkle root is available, the card can include the Clanker v4 `airdrop` object with total amount, Merkle root, lockup, optional vesting, and optional configured admin.

The legacy command flow remains available:

```text
[p]clanker launch TICKER "Token Name" 1000000 0xCreatorAddress... optional image URL and description
```

Both flows validate basic metadata and the creator's primary reward address, build a Base-chain launch payload, display it for review, record the request, and only POST to the configured API when submit mode is enabled. The payload uses Clanker v4-style `rewards.recipients` entries so the creator receives the primary creator-reward share and the configured SickGaming treasury receives the platform creator-reward share. Airdrops are optional and separate from creator rewards: airdrops allocate token supply, while rewards split LP/creator fees.

## Commands

- `[p]clanker card` - open the interactive launch-card flow with buttons/modals.
- `[p]clanker launch <symbol> <name> <supply> <creator_address> [image_url] [description]` - create a launch request with the legacy command flow.
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
- `[p]clankerset airdrop lockup <seconds>` - set airdrop lockup; minimum 604800 seconds / 7 days.
- `[p]clankerset airdrop vesting <seconds>` - set optional airdrop vesting; 0 disables vesting.
- `[p]clankerset airdrop admin [0x...]` - set or clear the optional airdrop admin address.
- `[p]clankerset audit clear` - clear the guild audit log.
