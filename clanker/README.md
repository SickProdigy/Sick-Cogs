# Clanker

Clanker prepares and orchestrates Clanker v4 token launches on Base Sepolia.

The cog supports immutable draft creation and review followed by either direct protected CryptoWallet signing from the verified Discord card or an external-wallet handoff with exact transaction verification. Obsolete REST submission and browser-based internal approval paths have been removed.

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

Start a prefilled interactive draft with the dedicated clanking command:

```text
[p]clank SGBT "SickGaming Bot Token"
```

The ticker is uppercased automatically. The token name is optional, so `[p]clank SGBT` opens the same form with only the ticker prefilled. `clank` is a standalone creation command, not an alias for the `clanker` management group. `[p]clanker launch` opens the same draft explicitly, and `[p]clanker card` opens an empty draft.

The requester can enter token basics, a separately configurable creator reward treasury, optional details, the configured vault, and an optional airdrop; preview the current payload; and save a bounded guild launch record. When CryptoWallet is loaded, its Discord-bound public Base Sepolia address is prefilled as the editable creator/token-admin default. The card is requester-bound so another member cannot edit or save it.

Clanker v4 uses its fixed 100 billion token supply; callers cannot override it. Drafts, launches, receipts, and reward state are stored under the requesting Discord user, with the origin server retained only for policy and moderator audit context. Personal drafts, launch history, receipt details, and reward portfolios can be viewed in DMs. Saved drafts remain resumable; Clanker creates a fresh 15-minute immutable execution window only when a wallet route is chosen. The execution wallet, token administrator, creator reward treasury, and platform treasury are independently validated roles. The creator reward treasury defaults to the token administrator but is editable; the bot-owner platform share and treasury are deployment-global and are not user-overridable. After verification, **Launch with CryptoWallet** submits the exact card directly when delegated signing is active. If authorization is missing, the bot sends the general signed authorization link; return to the same verified card afterward. The external-wallet route provides a requester-bound companion link and verifies the submitted transaction directly against Base Sepolia.

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

- `[p]clank <symbol> [token name]` — start clanking a new token; this is separate from the `clanker` management group.

- `[p]clanker card` — open an empty interactive draft flow.
- `[p]clanker launch <symbol> [token name]` — open the normal interactive flow with its ticker and optional name prefilled.
- `[p]clanker status` — show draft and guild-control configuration.
- `[p]clanker drafts [limit]` — show readable unsubmitted drafts and reopen editable or verified cards from a requester-bound selector.
- `[p]clanker draft <launch_id>` — show one of your saved drafts by compact reference.
- `[p]clanker draftremove <launch_id>` — review and confirm deletion of one unsubmitted draft.
- `[p]clanker draftsremoveall` — review and confirm deletion of all your unsubmitted drafts; launch activity is never included.
- `[p]clanker launches [limit]` — open current launch and reward cards from requester-bound controls; server and DM history use the same requester-bound navigator and current launch receipts.
  Never-submitted CryptoWallet approvals offer **Resume approval** on the same renewed immutable record. Failed internal attempts offer **Retry as new draft** and confirmed removal; submitted, pending, uncertain, and confirmed cards remain read-only except for confirmed reward actions.
- A confirmed launch card’s **Rewards** button opens its one-coin gas review. **Collect via CryptoWallet** and **Collect via external wallet** are alternative routes for the same token-scoped collection. **Review withdrawable balances** is disabled when the preflight finds no deposited treasury balance.
- `[p]clanker claimall` — DM a paginated portfolio review, select tokens to collect, and review a WETH-profitable treasury-wide withdrawal separately.
- `[p]clanker rewardverify <launch_id> <transaction_hash>` — reconcile an external-wallet collection against its token administrator, exact locker call, and `ClaimedRewards` event.
- `[p]clanker claimplatform` — bot-owner review for profitable platform-treasury withdrawals only; creator balances cannot be included.
- `[p]clanker dismiss <launch_id>` — hide a failed, uncertain, or abandoned attempt from your list while retaining the moderator audit record.
- `[p]clanker audit [limit]` — view all recent draft and launch records (moderator).

The first launch of a symbol for each requester uses the lowercase symbol as its command reference (for example, `nmt`). Additional launches of the same symbol receive a short suffix. Full immutable launch IDs remain accepted for compatibility and internal binding.
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

## Base Sepolia acceptance

Before treating either route as accepted, use a newly created draft and retain its short reference. For the internal route, verify the Discord card and press **Launch with CryptoWallet**; authorize the wallet through the general signed handoff only if prompted, then return to the card. Run `[p]clanker refresh <launch_id>` when a submitted or uncertain operation needs reconciliation. For the external route, open the short-lived DM companion link, submit from the intended wallet, then run `[p]clanker verify <launch_id> <transaction_hash>`.

For each route, retain only public evidence: launch ID, execution route, transaction hash, deployed token address, block number, and final status. Confirm the transaction is on chain ID 84532, calls the pinned factory with zero value and exact stored calldata, emits one matching `TokenCreated` event for the stored admin, and leaves deployed bytecode at the reported token address. Do not record approval URLs, browser handles, provider credentials, private keys, or recovery material. A failed, rejected, expired, replayed, wrong-user, wrong-wallet, wrong-network, or mutated attempt must not become confirmed.

## Prototype boundary

- Base Sepolia only; mainnet and real assets remain out of scope.
- No private keys, seed phrases, wallet credentials, partner API tokens, or provider secrets are stored by this cog.
- Discord commands describe launch intent; they are not blockchain authorization.
- The generic REST submit route will not return. Only the reviewed CryptoWallet and external-wallet adapters may execute a launch.
