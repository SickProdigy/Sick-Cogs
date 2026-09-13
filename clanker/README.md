# Clanker

Clanker prepares and orchestrates Clanker v4 token launches on Base Sepolia.

The cog currently supports draft creation and review. The obsolete generic partner REST submission path and API-token configuration have been removed. Wallet execution will be added through two explicit adapters:

- protected signing with the requesting user’s CryptoWallet; and
- a TokenFactory-style external-wallet handoff with exact transaction verification.

Clanker owns launch construction, field validation, previews, limits, audit records, execution-method selection, and final launch status. CryptoWallet remains an optional narrow signer and never gives Clanker access to CDP credentials or generic transaction authority.

## Setup

```text
[p]clankerset treasury <0x...>
[p]clankerset platformbps 2000
[p]clankerset externalurl https://example.org/clanker/external.html
[p]clankerset channel #token-launches
[p]clankerset approvalchannel #token-launch-log
[p]clankerset allowedrole @Launchers
[p]clankerset blockedrole @Blocked
[p]clankerset cooldown 60
[p]clankerset dailymax 3
[p]clankerset vault percentage 10
[p]clankerset vault lockup 604800
[p]clankerset vault enabled true
[p]clankerset enabled true
```

Clanker now constructs and stores the exact pinned `deployToken` operation with each immutable launch intent. The same operation will feed either protected CryptoWallet signing or the external-wallet handoff; neither adapter may alter its target, value, or calldata.

Host `clanker/web/` as static HTTPS assets, then configure the exact `external.html` URL with `[p]clankerset externalurl <https-url>`. The page processes the DM attachment entirely in the browser; it has no relay endpoint and receives no wallet secrets.

The treasury receives the configured platform share of creator rewards. The default is 2000 bps (20%).

## User flow

Open the interactive draft card:

```text
[p]clanker card
```

The requester can enter token basics, optional details, the configured vault, and an optional airdrop; preview the current payload; and save a bounded guild launch record. The card is requester-bound so another member cannot edit or save it.

The legacy text command also prepares a draft:

```text
[p]clanker launch TICKER "Token Name" 0xCreatorAddress
```

Clanker v4 uses its fixed 100 billion token supply; callers cannot override it. Saved drafts can enter protected CryptoWallet approval with `[p]clanker internal <launch_id>`. Clanker passes only its immutable launch and exact operation to CryptoWallet; the external-wallet route provides a requester-bound operation file and verifies the submitted transaction directly against Base Sepolia.
- `[p]clanker refresh <launch_id>` — synchronize a persisted CryptoWallet launch after approval or restart.

## Vaults

Owner-configured vault defaults allocate a whole percentage of the fixed supply. Clanker v4 requires at least a seven-day lockup, supports optional linear vesting, and defaults the recipient to the launch token admin. Vault plus airdrop allocations cannot exceed 90% of supply.

## Airdrops

Airdrop rows may use fixed whole-token amounts or percentages of the fixed supply:

```text
0x1111111111111111111111111111111111111111=1%
0x2222222222222222222222222222222222222222=250000000
```

Recipient lists generate an OpenZeppelin `StandardMerkleTree`-compatible root and proof export matching the reviewed `clanker-sdk@4.2.19` schema. The allocation must be at least 25 bps, cannot exceed 90% of supply, and has a minimum one-day lockup.

## Commands

- `[p]clanker card` — open the interactive draft flow.
- `[p]clanker launch ...` — prepare a draft with the text command.
- `[p]clanker status` — show draft and guild-control configuration.
- `[p]clanker audit [limit]` — view recent launch records.
- `[p]clanker launches [limit]` — list launch IDs.
- `[p]clanker internal <launch_id>` — DM the requester a protected CryptoWallet approval link.
- `[p]clanker refresh <launch_id>` — synchronize a persisted CryptoWallet launch after approval or restart.
- `[p]clanker external <launch_id>` — DM the requester the exact Base Sepolia operation.
- `[p]clanker verify <launch_id> <transaction_hash>` — verify the exact transaction, TokenCreated event, and deployed bytecode.
- `[p]clanker launchinfo <launch_id>` — show one launch record.
- `[p]clanker airdropproofs <launch_id>` — export generated proof metadata.
- `[p]clankerset view` — show owner configuration.
- `[p]clankerset enabled <true|false>` — enable draft creation.
- `[p]clankerset externalurl [https-url]` — configure or clear the hosted external-wallet page.
- `[p]clankerset treasury <0x...>` — configure the platform treasury.
- `[p]clankerset platformbps <0-10000>` — configure the reward split.
- `[p]clankerset channel [#channel]` / `clearchannel` — manage the launch channel.
- `[p]clankerset approvalchannel [#channel]` / `clearapprovalchannel` — manage the audit-summary channel.
- `[p]clankerset allowedrole <@role>` / `clearallowedrole` — manage creator access.
- `[p]clankerset blockedrole <@role>` / `clearblockedrole` — manage blocked access.
- `[p]clankerset cooldown <seconds>` — configure the per-user draft cooldown.
- `[p]clankerset dailymax <number>` — configure the rolling daily draft limit.
- `[p]clankerset vault ...` — manage vault allocation, lockup, vesting, recipient, and enablement.
- `[p]clankerset airdrop ...` — manage default airdrop data and proof exports.
- `[p]clankerset audit clear` — clear the guild audit log.

## Prototype boundary

- Base Sepolia only; mainnet and real assets remain out of scope.
- No private keys, seed phrases, wallet credentials, partner API tokens, or provider secrets are stored by this cog.
- Discord commands describe launch intent; they are not blockchain authorization.
- The generic REST submit route will not return. Only the reviewed CryptoWallet and external-wallet adapters may execute a launch.
