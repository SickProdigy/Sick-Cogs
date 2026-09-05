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
[p]clankerset channel #token-launches
[p]clankerset approvalchannel #token-launch-log
[p]clankerset requireapproval true
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

The card lets the requester fill token basics in Discord modals, add optional details, add or clear an optional airdrop, preview the payload, and click **Submit Launch**. The card is bound to the user who opened it so other members cannot edit or submit that draft. When recipient rows are entered for an airdrop, the cog deterministically generates a Merkle root and proof export using the documented export schema. When live submit mode is enabled, the first submit click arms a final confirmation summary and the requester must click **Submit Launch** a second time before any API call is made.

Airdrop entries can be entered as fixed token amounts or percentages of the draft supply:

```text
0x1111111111111111111111111111111111111111=1%
0x2222222222222222222222222222222222222222=250000000
0x3333333333333333333333333333333333333333 0.25%
```

Percentages are converted to whole token amounts using the draft supply, the total airdrop allocation is capped at 90% of supply to match Clanker extension limits, and lockup is enforced at Clanker's documented 7-day minimum. Recipient-list airdrops generate a root/proof export with this schema:

```text
keccak256(abi.encode(uint256 index,address account,uint256 amount)); sorted-pair Merkle tree
```

The launch record stores the generated proof metadata so moderators can export it later. If a prebuilt Merkle root is supplied manually, the card verifies it against the entered recipient rows before accepting it. If only a total amount and prebuilt Merkle root are provided, the cog can include the Clanker v4 `airdrop` object but cannot export recipient proofs.

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
- `[p]clanker launches [limit]` - list recent launch records with launch IDs.
- `[p]clanker launchinfo <launch_id>` - show one launch record with token, reward, airdrop, and API reference details.
- `[p]clanker airdropproofs <launch_id>` - export generated airdrop proof metadata for a launch record.
- `[p]clanker approve <launch_id>` - bot-owner only: approve and live-submit a pending launch record.
- `[p]clanker reject <launch_id> [reason]` - bot-owner only: reject a pending launch record without submitting it.
- `[p]clankerset view` - show configuration without secrets.
- `[p]clankerset enabled <true|false>` - enable or disable the cog.
- `[p]clankerset submit <true|false>` - enable or disable live API submissions.
- `[p]clankerset apiurl <url>` - set the Clanker API base URL.
- `[p]clankerset apitoken <token>` - store the Clanker API bearer token.
- `[p]clankerset treasury <0x...>` - set the bot-owner/SickGaming platform treasury address.
- `[p]clankerset platformbps <0-10000>` - set the bot-owner reward cut, default 2000 bps / 20%.
- `[p]clankerset channel [#channel]` - restrict launch creation to one channel; omit the channel to use the current channel.
- `[p]clankerset clearchannel` - allow launch creation in any channel.
- `[p]clankerset approvalchannel [#channel]` - post launch record summaries to a review/log channel; omit the channel to use the current channel.
- `[p]clankerset clearapprovalchannel` - stop posting launch record summaries to a review/log channel.
- `[p]clankerset requireapproval <true|false>` - require bot-owner approval before live API submission.
- `[p]clankerset allowedrole <@role>` - require a role before members can create launch requests.
- `[p]clankerset clearallowedrole` - clear the launch-request role requirement.
- `[p]clankerset blockedrole <@role>` - block a role from creating launch requests.
- `[p]clankerset clearblockedrole` - clear the blocked role.
- `[p]clankerset cooldown <seconds>` - set a per-user launch cooldown; 0 disables it.
- `[p]clankerset dailymax <number>` - set a per-user rolling 24-hour launch limit; 0 disables it.
- `[p]clankerset airdrop enabled <true|false>` - enable or disable configured airdrop payloads.
- `[p]clankerset airdrop build <supply> <recipient rows>` - generate and store a default airdrop Merkle root/proof export from recipient rows.
- `[p]clankerset airdrop export` - export the currently stored configured airdrop proof metadata.
- `[p]clankerset airdrop root <0x...>` - set the 32-byte Merkle root for the prepared airdrop list.
- `[p]clankerset airdrop amount <tokens>` - set total token amount reserved for the airdrop.
- `[p]clankerset airdrop lockup <seconds>` - set airdrop lockup; minimum 604800 seconds / 7 days.
- `[p]clankerset airdrop vesting <seconds>` - set optional airdrop vesting; 0 disables vesting.
- `[p]clankerset airdrop admin [0x...]` - set or clear the optional airdrop admin address.
- `[p]clankerset audit clear` - clear the guild audit log.

## Current implementation status

Completed in the first milestone:

1. Non-custodial Clanker launch-helper foundation.
2. Owner-managed API URL, bearer token, treasury, reward split, submit mode, and airdrop defaults.
3. Review-first dry-run launch records with bounded guild audit storage.
4. Legacy command launch flow for direct token payload preparation.
5. Interactive Discord launch card with requester-bound modals/buttons.
6. Optional fixed-amount or percentage airdrop entry parsing.
7. Deterministic Merkle-root/proof generation and proof export for recipient-list airdrops.
8. Clanker 7-day airdrop lockup minimum enforcement.
9. Final confirmation summary before live API submission from the launch card.
10. Launch IDs, richer audit records, recent launch listing, and launch-record detail embeds.
11. Guild controls for launch channels, review/log channels, owner approval before live submission, allowed/blocked roles, configurable cooldowns, and per-user daily launch limits.

Remaining work before calling the cog complete:

1. Verify the exact live Clanker API endpoint, auth headers, payload response shape, status polling, and failure recovery against production API access.
2. Verify generated airdrop schema against Clanker's exact claim tooling/contract before relying on it for production claims.
3. Add post-submit status polling once Clanker response IDs and status endpoints are confirmed.
4. Decide whether launch approval can safely be delegated to trusted moderators or should stay bot-owner only.
5. Split the cog into focused modules as it grows: commands, admin settings, config/migrations, models, validation, Clanker API client, airdrops, and Discord views.
6. Add tests for validation, amount parsing, airdrop caps, payload construction, submit-mode blockers, and bad Discord modal input.
7. Validate richer metadata/media fields such as image type/size, website/social links, and any Clanker-supported Farcaster fields.
8. Track NFT/image minting as a separate feature path unless the Clanker token-launch API grows to cover it directly.

## Production boundary

Until the live API contract and airdrop proof workflow are verified:

- keep submit mode disabled except during controlled review;
- never store private keys, seed phrases, or wallet-export material in Discord bot config;
- do not treat Discord identity as blockchain signing authority;
- do not describe creator rewards as token supply ownership;
- require an explicit review/confirmation step before irreversible mainnet actions.
