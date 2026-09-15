# Clanker

Clanker prepares and orchestrates Clanker v4 token launches on Base Sepolia.

The cog supports immutable draft creation, review, and two explicit Base Sepolia execution adapters. The obsolete generic partner REST submission path and API-token configuration have been removed:

- protected signing with the requesting user’s CryptoWallet; and
- a TokenFactory-style external-wallet handoff with exact transaction verification.

Clanker owns launch construction, field validation, previews, limits, audit records, execution-method selection, and final launch status. CryptoWallet remains an optional narrow signer and never gives Clanker access to CDP credentials or generic transaction authority.

## Setup

```text
[p]clankerset treasury <0x...>
[p]clankerset platformbps 2000
[p]walletset approvalurl https://example.org/cryptowallet
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

Clanker constructs and stores the exact pinned `deployToken` operation with each immutable launch intent. The same operation feeds either protected CryptoWallet signing or the external-wallet handoff; neither adapter may alter its target, value, or calldata.

The external-wallet controls live on CryptoWallet’s shared companion session page. Configure that companion once with `[p]walletset approvalurl <https-url>`; Clanker reads the same base URL used by TokenFactory. The page processes the DM attachment entirely in the browser and receives no wallet secrets.

The treasury receives the configured platform share of creator rewards. The default is 2000 bps (20%).

## User flow

Open a prefilled interactive draft card with the friendly shortcut:

```text
[p]clank SGBT "SickGaming Bot Token"
```

The ticker is uppercased automatically. The token name is optional, so `[p]clank SGBT` opens the same form with only the ticker prefilled. `[p]clanker card` remains available as an empty administrative draft.

The requester can enter token basics, a separately configurable creator reward treasury, optional details, the configured vault, and an optional airdrop; preview the current payload; and save a bounded guild launch record. When CryptoWallet is loaded, its Discord-bound public Base Sepolia address is prefilled as the editable creator/token-admin default. The card is requester-bound so another member cannot edit or save it.

The legacy text command also prepares a draft:

```text
[p]clanker launch TICKER "Token Name" 0xCreatorAddress
```

Clanker v4 uses its fixed 100 billion token supply; callers cannot override it. Saved drafts remain resumable; Clanker creates a fresh 15-minute immutable execution window only when a wallet route is chosen. The execution wallet, token administrator, creator reward treasury, and platform treasury are independently validated roles. The creator reward treasury defaults to the token administrator but is editable; the server-configured platform share and treasury are not user-overridable. Saved drafts can enter protected CryptoWallet approval with `[p]clanker internal <launch_id>`. Clanker passes only its immutable launch and exact operation to CryptoWallet; the external-wallet route provides a requester-bound protected companion link and verifies the submitted transaction directly against Base Sepolia.
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

- `[p]clank <symbol> [token name]` — open the normal interactive flow with its ticker and optional name prefilled.
- `[p]clanker card` — open an empty interactive draft flow.
- `[p]clanker launch ...` — prepare a draft with the text command.
- `[p]clanker status` — show draft and guild-control configuration.
- `[p]clanker drafts [limit]` — list your saved, unsubmitted drafts.
- `[p]clanker draft <launch_id>` — show one of your saved drafts.
- `[p]clanker launches [limit]` — list your records that entered an execution route using compact, requester-bound references.
- `[p]clanker dismiss <launch_id>` — hide a failed, uncertain, or abandoned attempt from your list while retaining the moderator audit record.
- `[p]clanker audit [limit]` — view all recent draft and launch records (moderator).

The first launch of a symbol for each requester uses the lowercase symbol as its command reference (for example, `nmt`). Additional launches of the same symbol receive a short suffix. Full immutable launch IDs remain accepted for compatibility and internal binding.
- `[p]clanker internal <launch_id>` — DM the requester a protected CryptoWallet approval link.
- `[p]clanker refresh <launch_id>` — synchronize a persisted CryptoWallet launch after approval or restart.
- `[p]clanker external <launch_id>` — DM the requester the exact Base Sepolia operation.
- `[p]clanker verify <launch_id> <transaction_hash>` — verify the exact transaction, TokenCreated event, and deployed bytecode.
- `[p]clanker launchinfo <launch_id>` — show one launch record.
- `[p]clanker airdropproofs <launch_id>` — export generated proof metadata.
- `[p]clankerset view` — show owner configuration.
- `[p]clankerset enabled <true|false>` — enable draft creation.
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

## Base Sepolia acceptance

Before treating either route as accepted, use a newly created draft and record its launch ID. For the internal route, approve through the OAuth-protected CryptoWallet page, then run `[p]clanker refresh <launch_id>` until the stored status is confirmed. For the external route, open the short-lived DM companion link, submit from the intended wallet, then run `[p]clanker verify <launch_id> <transaction_hash>`.

For each route, retain only public evidence: launch ID, execution route, transaction hash, deployed token address, block number, and final status. Confirm the transaction is on chain ID 84532, calls the pinned factory with zero value and exact stored calldata, emits one matching `TokenCreated` event for the stored admin, and leaves deployed bytecode at the reported token address. Do not record approval URLs, browser cookies, OAuth state, provider credentials, private keys, or recovery material. A failed, rejected, expired, replayed, wrong-user, wrong-wallet, wrong-network, or mutated attempt must not become confirmed.

## Prototype boundary

- Base Sepolia only; mainnet and real assets remain out of scope.
- No private keys, seed phrases, wallet credentials, partner API tokens, or provider secrets are stored by this cog.
- Discord commands describe launch intent; they are not blockchain authorization.
- The generic REST submit route will not return. Only the reviewed CryptoWallet and external-wallet adapters may execute a launch.
