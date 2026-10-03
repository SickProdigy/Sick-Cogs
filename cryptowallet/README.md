# Crypto Wallet

Crypto Wallet is an experimental, Base-first wallet cog for Red-DiscordBot. Its intended user experience is bot-first: a user's wallet is provisioned automatically when they first interact with the wallet commands, and the bot immediately returns a public deposit address.

The Base mainnet release candidate is tracked in issues #202 and #237. Reviewed testnet networks remain enabled; Base mainnet stays default-off and emergency-paused until the complete ordinary-member flow passes develop acceptance.

## Multi-network safety boundary

CryptoWallet models each blockchain with an explicit chain family, network reference, native-token precision, testnet state, and independently reviewed capabilities for balances, sends, history, transaction lookup, delegation, recovery, export, and fee sponsorship. EVM chain IDs and Solana cluster names are deliberately different fields.

A capability must be enabled in both the network registry and the active provider adapter before a send can be created. Address validation is dispatched from the explicitly selected network, including independent 32-byte base58 validation for Solana addresses; a Solana address is never interpreted as EVM data. Transaction storage now also exposes network-neutral atomic amount and fee fields while retaining the existing Base wei keys for stored-profile compatibility.

Base Sepolia and Solana devnet are enabled by default for sends. Base mainnet has reviewed code-level capabilities, but every installation keeps them disabled and emergency-paused until the owner deliberately arms develop testing. Ethereum Sepolia, Arbitrum Sepolia, Polygon Amoy, and Avalanche Fuji are enabled only for their reviewed read-only capabilities. Solana devnet has a distinct CDP Solana account with native SOL balance, recent activity, transaction-signature lookup, explorer support, protected native-SOL sends, and isolated Coinbase key export. Solana tokens remain disabled.

CryptoWallet has one installation-wide operating mode controlled by the bot owner. New and upgraded installations default to `testnet`. Owners may select `mainnet` for fail-closed staging before execution is armed; ordinary wallet views and integrated product drafts are then mainnet-first while testnet remains available only through explicit testnet commands. This staging selection does not enable a transaction capability. `mainnet-only` rejects testnet commands without deleting testnet profiles, tokens, or history. Changing presentation mode never enables a network capability, creates a replacement wallet, or bypasses the mainnet release gate.

CryptoWallet `1.2.51` adds a narrow Polymarket funding adapter that creates and persists the ordinary protected Base-mainnet native-ETH transfer intent, binds it to one external Bridge request fingerprint, returns only public reconciliation evidence, and renders the existing owner-bound approval card. It reuses every Base mainnet term, pause, capability, balance, fee-threshold, daily-limit, final-refresh, authorization, and optional TOTP check; it provides no generic signing or submission bypass and remains default-off.

CryptoWallet `1.2.52` extends the existing signed five-minute Polymarket eligibility claim with the exact `deposit` action. The browser still checks the official geoblock endpoint from the protected user's IP and returns only bounded location eligibility evidence; it receives no deposit amount, provider credential, key, or signing authority.

CryptoWallet `1.2.53` extends that signed claim with exact `buy` and `sell` schemas. Each five-minute handoff binds the public account and session identities plus only the market path, outcome, amount, and price bound required for that side; malformed, extra, secret-bearing, or cross-action fields fail closed in both the server and browser validators.

CryptoWallet `1.2.54` adds an exact-purpose signer for Polymarket Deposit Wallet settlement batches. It accepts only the pinned current CTF adapter or protocol-v3 router redemption calldata, binds the wallet, nonce, ten-minute deadline, and user-approved fingerprint, and repeats every check inside the provider adapter before requesting one CDP signature. The shared signing switch remains default-off; this adapter cannot submit a relayer request or transaction.

CryptoWallet `1.2.55` extends the protected browser-IP eligibility claim with an exact `claim` action. It binds only the requesting user, owner signer, Deposit Wallet, market path, and outcome; session identities, amounts, prices, credentials, signatures, and extra fields are rejected by both the server issuer and browser verifier. Eligibility evidence does not authorize or submit settlement.

Base Mainnet is a reviewed, installation-gated network definition for chain ID 8453. Its balance, token discovery, send, history, transaction lookup, delegation, recovery, and export code paths are available only after the installation gate and each capability are deliberately enabled; sponsorship remains unavailable. In testnet mode the existing `base` alias continues to mean Base Sepolia, while mainnet mode routes explicit mainnet activity through Base mainnet. Polygon Mainnet, OP Mainnet, BNB Chain, and Zora remain disabled metadata-only definitions. Polygon Mainnet is reserved for the separately reviewed Polymarket connector (chain ID 137, pUSD collateral); it has no enabled transaction capability today. CryptoWallet can now expose one secret-free Polymarket signer context by re-reading the CDP end user and proving that the stored smart account has exactly one registered EOA owner. The exact delegated end-user EIP-712 adapter is implemented for Polymarket's current ClobAuth proof and Deposit Wallet session authorization/revocation Batch payloads, but its CryptoWallet switch defaults off and has no enable command yet. Returned signatures must be independently recovered to the bound EOA. This does not enable Polygon, deploy an account, or submit a transaction. The other entries use CDP's documented network identifiers. They have no enabled balance, discovery, history, send, delegation, or sponsorship capability and do not appear in ordinary wallet cards. Future mainnet sends must use user-funded native gas; this project does not promise bot-funded gas. Enabling any of these entries still requires the separate mainnet security, legal, policy, fee-estimation, and transaction-verification review. The shared Polymarket handoff contract is likewise metadata-only: Polygon chain 137 and pUSD are named so a later adapter has an explicit target, but it is disabled and cannot provision accounts, read balances, approve collateral, or sign a transaction.

Bot owners can validate the official Base RPC chain identity, the immutable reviewed CDP provider contract, and the configured `cryptowallet_cdp` project credentials with `[p]walletset mainnet preflight`; the check is read-only and cannot create a wallet, delegation, policy, or transaction. The provider contract pins Base chain ID 8453, the ERC-4337 smart-account/EOA-owner relationship, owner-key export boundary, fee-quote requirement, spend-permission fields, operation statuses and identifiers, and the exact reviewed executable capability set, with sponsorship excluded. Any drift fails before credentials are read. CDP credentials are project-scoped and intentionally shared across Base Sepolia and Base mainnet, matching CDP network-scoped account behavior. Final mainnet validation still selects only a stored `base-mainnet` account and an explicit Base-mainnet operation; wallet identity, chain ID, authorization, and operation state cannot fall back to a Base Sepolia or Solana account. Bot owners can inspect this boundary with `[p]walletset mainnet status` and force it closed with `[p]walletset mainnet pause`. Individual controls use `[p]walletset mainnet capability <name> <status|disable|enable>`; disabling is immediate, while enabling requires both the exact permanent-loss acknowledgement and an already reviewed code-level capability. Set all three native-ETH ceilings atomically with `[p]walletset mainnet limits <per-transaction> <per-user-day> <installation-day>`; the command requires transaction ≤ user-day ≤ installation-day and does not enable mainnet. Reviewed Base-mainnet submissions must reserve daily capacity by immutable intent ID in persistent Red configuration before provider submission; duplicate reservations remain idempotent across restarts, while changed intent data fails closed. Every real-value approval card must also bind and display the Base chain ID, amount, destination, recipient count, gas estimate and reapproval threshold, gas payer, existing authorization requirement, irreversibility, and permanent-loss warning; an unavailable fee estimate or reapproval threshold blocks approval. Discord confirmation approves only the immutable displayed intent and does not require a second companion visit. Final approval also revalidates the capability-reviewed network, provider send support, wallet profile, exact network account, and normalized sender and destination before consuming an optional authenticator code or entering the submission state. Immediately before that boundary, the provider adapter also confirms live EVM chain identity, active authorization, the approved fee policy, and that no provider operation or public transaction identifier already exists. ERC-4337 and Solana nonces remain provider-managed when the idempotent submission is created; the cog therefore requires a clean not-yet-created operation instead of applying an EOA nonce check to a smart account. Optional step-up verification uses standard RFC 6238 TOTP codes compatible with Authy and other authenticator apps, a bounded clock window, and rejection of a previously accepted time counter. It does not use the legacy Authy API. Browser enrollment seeds are generated client-side and may enter the bot only as RSA-OAEP-SHA256 ciphertext encrypted to a dedicated server-held key; the public enrollment key contains no secret material. Pending and enabled enrollment state stores only AES-256-GCM ciphertext in Red configuration; its separate server-only encryption key remains in Red shared API tokens, and authenticated encryption binds the secret to this deployment, Discord user, and immutable wallet profile. Users enroll with `[p]wallet security 2fa setup`: a one-time protected page generates a 160-bit seed in browser memory, submits only RSA-OAEP ciphertext, and displays a locally generated standard TOTP QR code with the manual Authy-compatible key as fallback; activation then requires the current code through an owner-bound private Discord modal. Existing enabled enrollment cannot be overwritten by setup. `[p]wallet security 2fa disable` and `replace` require the current factor through an owner-bound private modal; replacement removes the old factor first and directs the user through a fresh protected setup. Lost-factor recovery requires immediate emergency lock plus the bot-owner-only `walletset 2fareset` command, which refuses to run unless the wallet is locked and the owner supplies the exact post-identity-review acknowledgement. For opted-in accounts, pressing Approve opens a private Discord modal for a six-digit code; successful verification is bound to the exact immutable transaction fingerprint and is consumed only after quote refresh and final balance checks pass. Accounts without TOTP retain the existing approval flow. The guarded enable command requires the exact permanent-loss acknowledgement. Code review alone does not enable mainnet: the installation gate, emergency pause, individual capability flags, and configured limits still fail closed.

Ethereum Sepolia smart-account operations cannot assume Base gas sponsorship. CDP's built-in Paymaster supports Base networks; Ethereum Sepolia must use user-funded test ETH or a separately reviewed compatible paymaster.

See [Polygon / Polymarket handoff boundary](docs/polygon-polymarket-handoff.md) for the develop-only provider-neutral execution decision record.
Release reviewers and operators must also read the [CryptoWallet threat model](docs/threat-model.md) and [operations and incident runbook](docs/operations-runbook.md). These documents describe current trust boundaries and containment procedures; they do not enable mainnet.

## Intended experience

```text
User runs the wallet command for the first time
→ Service creates an internal wallet profile
→ Wallet provider creates user-associated EVM and Solana testnet accounts
→ Bot immediately displays the enabled testnet portfolio and public addresses
→ Wallet can receive deposits
```

Routine account information remains available through Discord:

- public wallet address
- explicit network and chain ID or Solana cluster
- balance and deposit information
- transaction-intent creation and status
- public transaction hashes and confirmations

Provider-backed wallet summaries are limited to one request per user every 10 seconds, and new
transaction-history cards to one every 15 seconds. Bot owners and server administrators bypass
these limits. History remains compact at 10 entries per page, protects new requests from rapid repeat
clicks, and includes a permanent explorer link for complete public history. EVM history uses
Etherscan V2 first when the shared `etherscan.api_key` token is configured; the owner can select
`auto`, `etherscan`, `cdp`, or `explorer-only` routing. Identical requests are coalesced and cached
for 30 seconds, with bounded response bodies, cache size, and installation-wide concurrency.
The repeatable synthetic load benchmark uses no provider credentials or network requests. On the
authorized SGBTestAgent environment, 1,000 distinct requests stayed at eight active loads, 1,000
identical requests coalesced to one provider call, the cache stopped at 512 entries, and peak RSS
increased by approximately 3.9 MiB. Run it with
`python -m cryptowallet.tests.benchmark_history_load` in the representative Red environment.
The plural `wallets` command is accepted as an alias for `wallet`. Other CDP-backed commands
and public RPC lookups have separate per-user guards; local notification and network commands do
not. CDP traffic is also globally limited to half the published rolling read/write ceilings.

The browser interface is not an enrollment requirement. It is an independent account-control surface for sensitive operations:

- authorizing limited bot actions for an automatically provisioned wallet
- recovery and backup configuration
- signer or key export where supported
- emergency signer backup without wallet or provider-account deletion
- authentication-method management
- reviewing and revoking application signers
- granting restricted delegated authority
- approving transactions outside delegated policy

## Transaction model

Discord commands express intent; they do not independently prove blockchain authorization.

```text
User runs a wallet or trading command
→ Bot creates a typed transaction intent
→ Existing delegated policy is evaluated
→ Permitted actions may execute through the restricted application signer
→ Other actions require protected browser approval
→ Bot reports the public transaction result
```

Transfers require explicit Discord approval and an active, time-limited, user-scoped CDP delegation. Users can revoke that delegation independently without deleting their wallet or moving funds.

## Custody and identity

Each Discord user receives a distinct wallet profile, a Base Sepolia smart account controlled by that user’s exportable signer EOA, and a separate Solana account provisioned by CDP for devnet testing. A blockchain address cannot be deleted and may still receive funds, but deleting its provider identity or ejecting its signer could remove the supported way to operate it and strand those funds. CryptoWallet therefore exposes neither action. Coinbase Developer Platform (CDP) is the provisional wallet provider, but provider-specific behavior must remain behind an internal adapter.

Red user-data deletion first attempts to revoke the profile-wide bot signing delegation and then
unconditionally removes the Discord-side profile, intent, session, notification, and security metadata.
A provider outage cannot block the local privacy deletion, and no retry tombstone is retained. The CDP
wallet identity and public blockchain history are not deleted; users should export or transfer test assets
before requesting deletion if they need continued access.

SickGaming maintains its own wallet-profile identifier. External identities are verified links rather than primary keys:

```text
Wallet profile
├── CDP end-user identity
├── Discord immutable user ID
├── MyBB immutable user ID
├── optional Telegram immutable user ID
├── EVM owner signer
├── Base smart account
└── Solana devnet account
```

Never merge accounts by username, display name, supplied platform ID, or wallet address alone.

## Companion site

The shared browser assets live in [`web/`](web/) and are intended to be published at `https://sickgaming.net/cryptowallet`. They serve CryptoWallet authorization and recovery plus external-wallet handoffs owned by TokenFactory and Clanker.

CryptoWallet authorization uses a three-minute signed JWT in the URL fragment. Recovery and external-wallet operations use opaque one-time handles registered by the bot through the authenticated outbound PHP/MySQL relay described in [`web/server/README.md`](web/server/README.md). The cog exposes no inbound HTTP listener and requires no website pairing or Discord OAuth bridge. Ordinary wallet provisioning, balances, activity, and authorized signing remain bot-first.

Browser assets never receive CDP API secrets, the JWT private key, raw private keys, recovery phrases, relay secrets, or unrestricted signing credentials.

## Current commands

User commands:

```text
[p]wallet
[p]wallet @member
[p]wallet balance
[p]wallet networks
[p]wallet send <address> <amount>            # Base Sepolia default
[p]wallet send @member <amount>              # Provision recipient if needed
[p]wallet send base <address> <amount>
[p]wallet send base @member <amount>
[p]wallet send sol <address> <amount>
[p]wallet send sol @member <amount>
[p]wallet send <asset> base <address-or-member> <amount> # Registered Base Sepolia ERC-20
[p]wallet intent <bot-reference>
[p]wallet txid <network> <txid-or-signature>
[p]wallet transactions [network] # Aliases: tx, trans, history
[p]wallet token [network]
[p]wallet token add <network> <contract>
[p]wallet token default
[p]wallet token default <network> [native|symbol|contract]
[p]wallet token default reset
[p]wallet mode                    # Show the owner-controlled operating mode
[p]wallet testnet                  # Explicit sandbox in mainnet mode
[p]wallet testnet balance [network]
[p]wallet testnet networks
[p]wallet testnet tokens
[p]wallet testnet transactions [network]
[p]wallet testnet send [...]
[p]walletset environment [testnet|mainnet|mainnet-only]
[p]wallet notifications [true|false]
[p]wallet security              # Show emergency-lock status
[p]wallet security lock         # Alias: freeze; only bot owner can unlock
[p]wallet security 2fa          # Show optional authenticator status
[p]wallet security 2fa setup    # Protected Authy-compatible enrollment
[p]wallet security 2fa disable  # Requires the current authenticator code
[p]wallet security 2fa replace  # Verify current factor, then enroll again
[p]wallet security 2fa lost     # Lock immediately and contact the bot owner
[p]wallet authorize
[p]wallet auth [days]           # Short alias; optional days prefill
[p]wallet authorization
[p]wallet recovery             # Aliases: recover, backup
[p]wallet revoke                # Aliases: deauthorize, de-auth, deauth
```

The two-argument send form uses the member's selected default asset. Without a
member selection it follows the server's default network and native token. ERC-20
sends are currently limited to reviewed Base Sepolia tokens in the shared registry;
the cog verifies the contract metadata and balance again before approval. If two
registered contracts share a symbol, use the exact contract address.

Owner commands:

```text
[p]walletset view
[p]walletset usage
[p]walletset history
[p]walletset history mode <auto|etherscan|cdp|explorer-only>
[p]walletset lock <mention-or-user-id>      # Alias: freeze
[p]walletset unlock <mention-or-user-id>    # Alias: unfreeze
[p]walletset 2fareset <user-id> I CONFIRM IDENTITY REVIEW AND RESET 2FA
[p]walletset pause
[p]walletset resume
[p]walletset reconcile <mention-or-user-id> <bot-reference>
[p]walletset sendlimit [network] [amount|clear]
[p]walletset delegationdays [1-365]
[p]walletset delegationmaxdays [1-365]
[p]walletset emoji
[p]walletset emoji sync
[p]walletset emoji set <network> <emoji-id>
[p]walletset emoji clear <network>
[p]walletset cdpstatus
[p]walletset cdpcheck
[p]walletset mainnet preflight       # Read-only chain and isolated-credential check
[p]walletset mainnet status
[p]walletset mainnet pause
[p]walletset jwtstatus
[p]walletset jwksfile
[p]walletset approvalurl https://sickgaming.net/cryptowallet
[p]walletset clearapprovalurl
```

### Discord application emojis

Discord-ready chain images are packaged in
[`data/assets/app-emoji/`](data/assets/app-emoji/). Each file is a 128 × 128 transparent PNG,
is below 256 KB, and uses a valid application-emoji name. In the Discord Developer
Portal, open the same application used by this bot, open **Emojis**, and upload
the desired files without renaming them. Let the cog discover every matching
application emoji by name in one step:

```text
[p]walletset emoji sync
```

Use `[p]walletset emoji set <network> <emoji-id>` only to assign or replace an
individual mapping manually.

The same folder also includes future-use `optimism`, `bnb`, `zora`, `tron`, and
`linea` images. Those networks remain disabled and intentionally have no active
CryptoWallet emoji mapping until their capabilities and mainnet safety are reviewed.

`[p]walletset emoji` shows the current mappings. The network argument also accepts the
full registry key, such as `base-sepolia` or `polygon-amoy`. Use
`[p]walletset emoji clear <network>` to restore that network's built-in fallback symbol.
Application emojis belong to the Discord application and can be rendered by the bot
across its servers; they do not consume a server's emoji slots. Reload CryptoWallet
after installing an updated cog version, but changing an emoji ID takes effect on the
next wallet card without another reload.

Public wallet cards use one compact field per account family. The divider-styled EVM field groups its
supported-network icons, shared address, and tightly spaced nonzero balances; the Solana field
uses a matching divider heading and groups its address and balance. Addresses remain plain inline code for easier copying,
while each visible network name links to that address on the network's explorer.

`[p]walletset usage` reports UTC-month CDP reads and writes, Onchain Data reads, recent request
traffic, pending confirmation workload, conservative Embedded Wallet operation estimates, and
CDP Node billing-unit estimates. The wallet-operation safety target is 4,500 (90% of the published
5,000-operation free allowance), and the Node target is 7.5 million BU (75% of the published
10-million-BU allowance). Current public Base Sepolia RPC fallbacks add no CDP Node BU estimate.
Counters begin when this instrumentation is installed and remain estimates. Browser-direct CDP
authorization/delegation activity is not visible to the bot, so the CDP billing portal
is authoritative. Crossing 80%, 90%, or 100% of an internal target warns configured bot owners but
does not automatically stop operations. `walletset pause` and `walletset resume` provide deliberate
owner control.

The first `wallet`, `wallet authorize`, or `wallet send` command provisions the user's CDP end
user and its EVM and Solana accounts if no stored profile exists. Looking up a server member with
`wallet @member`, or sending to `@member`, also provisions that non-bot recipient on demand when
needed; it does not provision the server's member list in bulk or grant the recipient signing
authorization. Wallet creation, receiving, balances, and
other read-only commands require no signing authorization. The first approved send automatically requests
a protected authorization link when needed; `wallet authorize` provides the same flow for deliberate
reauthorization after revocation or expiry. Authorization handoff URLs expire after three minutes. The URL token stays in the fragment, is removed from browser history immediately,
and is validated by CDP custom authentication before the browser can grant a delegation for the
owner-configured duration (1–365 days, default 365). The expiry is signed into the handoff and
validated again by the browser before authorization. `walletset delegationdays` changes only new
authorizations; existing grants retain their current expiry. The grant covers the exact signed set
of provisioned EVM and Solana accounts. `wallet revoke` requires an owner-bound
Discord confirmation, revokes the user-scoped delegation across every account in the wallet profile,
and verifies with CDP that it is inactive; it does not delete the wallet or move funds. When authorization is already active, `wallet authorize` and `wallet authorization` show its current expiry and revocation control. CDP permits only one active user-scoped grant, so changing its duration requires revoking it first and then running `wallet auth [days]` again.

`wallet security lock` immediately persists an emergency lock, rejects pending send intents, and attempts to revoke the profile-wide bot signing delegation. While locked, receiving funds, balances, history, public transaction lookup, and authorization revocation remain available; new sends, approval clicks, authorization/renewal links, and signer export are blocked. Only the configured Red bot owner can remove the lock with `walletset unlock <mention-or-user-id>` after an independent identity review. An already-issued signed handoff can remain usable until its three-minute expiry, so the owner should retry delegation revocation if CDP was unavailable during locking. This is the current compromised-Discord response. Users may also enable the independent TOTP step-up described above; lost-factor recovery deliberately requires an emergency lock and bot-owner identity review rather than relying on Discord alone.

`wallet recovery` DMs a three-minute, purpose-bound, single-use link for backing up the user’s wallet signer. The URL contains only a random opaque handle. The public relay atomically consumes it and releases the encrypted-at-rest CDP handoff to the browser, where it is removed from browser history immediately. The browser validates the expected CDP user and account addresses and opens CDP’s isolated secure key-export iframe. The private key is copied within Coinbase’s iframe and is never exposed to the site JavaScript, Discord, or the bot. The smart account itself has no exportable private key; exporting its wallet signer EOA does not move funds or delete the provider account. Existing profiles require no ownership migration: CDP created the EOA owner with the smart account, and recovery resolves that relationship from the current `evmSmartAccountObjects.ownerAddresses` and `evmAccountObjects` response. Missing or ambiguous ownership fails closed.

A deliberate CDP exit is a sequence, not a delete button: export the existing EOA owner through `wallet recovery`; verify it in a compatible wallet without sharing the key; revoke the bot delegation with `wallet revoke`; and transfer every asset out of the smart account if the destination wallet cannot operate that ERC-4337 account. Keep enough native gas available for any unsponsored exit transaction. Export does not revoke CDP or relocate smart-account assets, and CryptoWallet never deletes the provider identity or ejects its signer because either action could strand funds.

`wallet send` accepts either a network-valid address or a current non-bot server-member mention,
then creates a 15-minute preview with owner-bound **Approve** and **Reject** buttons. Mentioned
recipients are resolved to the account family for the explicitly selected network.
Base Sepolia sends use CDP-sponsored smart-account operations and display a zero user-paid gas
fee. Solana devnet sends label the current cost as a network fee (EVM cards retain the technically
distinct gas-fee label) and submit a strict native System Program transfer. Before either
transaction is accepted as confirmed, its public-chain result must match
the stored sender, recipient, and exact atomic amount. Base Sepolia additionally requires 12
public-chain confirmations after the ERC-4337 event is independently recovered; Solana requires a
`finalized` RPC result. Provider `complete` alone is never treated as final. If an observed receipt
disappears, changes transaction hash, or moves to another block, the intent becomes **uncertain**
and requires evidence-based reconciliation instead of automatic replacement or resubmission.
Ethereum Sepolia and the additional EVM
testnets remain read-only because no reviewed, complete pre-approval fee path is available.

Approval checks CDP's authoritative profile-wide delegation status. When authorization is absent,
the bot DMs a short-lived authorization link and leaves the intent pending for another approval.
An unchanged quote atomically moves the intent into processing and uses a stable provider
idempotency key. The persistent global processor begins confirmation after roughly 20–30 seconds,
applies jittered backoff, survives reloads, and never resubmits merely because status is delayed.
`wallet intent <bot-reference>` displays private bot-operation state. An operation that remains
unconfirmed for 24 hours is escalated to **uncertain**, not failed; automatic polling stops and the
user is warned not to submit a replacement until its transaction ID and chain state are manually
reconciled. `wallet intent <bot-reference>` rechecks an uncertain operation when a stored TXID or
provider operation hash exists; owners can perform the same evidence-based check with
`walletset reconcile <mention-or-user-id> <bot-reference>`. A missing provider identifier remains
uncertain and cannot be overridden or guessed as failed.

`wallet transactions` without a network returns lightweight explorer links. Supplying `base`,
`eth`, or `sol` retrieves at most the latest ten supported activity records and links to complete
public history. EVM history is normalized behind a provider-neutral boundary; `etherscan` mode
never falls back to CDP, while `auto` uses bounded CDP history only if Etherscan is unavailable.
`wallet txid <network> <txid-or-signature>` performs an explicit-network public
lookup and does not expose private intent metadata. Arbitrum Sepolia, Polygon Amoy, and Avalanche
Fuji support explicit TXID lookup but not indexed activity.

## Current implementation status

CryptoWallet currently provides Base Sepolia and supported testnet wallet provisioning, public portfolio and activity reads, protected sends, revocable delegated signing, persistent confirmation recovery, signed browser authorization, and the outbound one-time relay used by recovery and external-wallet workflows. It intentionally has no inbound web server, website pairing protocol, or Discord OAuth session layer.

### CDP and custom-auth configuration

Sign in or create an account at the
[Coinbase Developer Platform Portal](https://portal.cdp.coinbase.com/), then create or select a
project. Under **API Keys → Secret API Keys**, create a Secret API Key; choose **Ed25519** when
Coinbase offers an algorithm choice. Copy its key ID and private secret when they are displayed.
Under the selected project's **Non-custodial Wallet → Security** area, generate the separate
Wallet Secret. Coinbase may display private values only once, so save them directly in an
appropriate server-side secret store and never post them in Discord messages, logs, or Git.

The four `cryptowallet_cdp` fields do not all come from the same screen:

| Red field | Meaning | Where it comes from | Secret? |
| --- | --- | --- | --- |
| `project_id` | Public identifier for the selected CDP project. The browser SDK will use it to select the same project as the cog. | The selected project's settings or Embedded Wallet configuration in the CDP Portal. | No |
| `api_key_id` | Identifier for a CDP Secret API Key used by the cog for authenticated server requests. | CDP Portal → API Keys → Secret API Keys → Create API key. Copy the displayed API key ID. | Treat as sensitive metadata |
| `api_key_secret` | Private half of that Secret API Key. It signs short-lived CDP API authentication tokens. | Shown once with the newly created Secret API Key. Save it when Coinbase displays it. | Yes |
| `wallet_secret` | Separate wallet-authentication secret used for sensitive wallet creation and signing operations. It is not the API key secret. | Generate it from the selected project's Non-custodial Wallet → Security page. Save it when Coinbase displays it. | Yes |

CryptoWallet separately generates a P-256 signing key during cog initialization and stores it in
Red's `cryptowallet_jwt` shared-token namespace. Its RFC 7638 thumbprint becomes `jwt_kid`; owners
do not invent or enter this value. The private key never goes to CDP, PHP, browser assets, or the
companion website. The public key is published as JWKS through `web/api/jwks.php`.

The API key must belong to the same CDP project as `project_id`. Never substitute a Coinbase
consumer account key, Advanced Trade key, wallet private key, seed phrase, Discord token, or any companion-site credential for any field above.

#### Required setup order

1. Create or select the CDP project and record its project ID.
2. Create an **Ed25519 Secret API Key** for that project and save its ID and secret. Set its IP
   allowlist to the bot/web server’s public outbound IP (normally one `/32` entry), not a Discord
   member’s or browser visitor’s IP. Enable **Account → Non-custodial → Manage** for the
   server-side wallet and delegation management used by CryptoWallet. **Export**, **Trade**, and
   **Transfer** are not required by this integration.
3. Generate the project’s separate **Wallet Secret** under **Wallets → Non-custodial Wallet →
   Security**, and save it when CDP displays it.
4. On that same **Security** page, enable **Delegated Signing**. This project-level switch is
   required for protected wallet authorization and is separate from the Secret API Key permissions.
5. As the Red bot owner, run `[p]set api`, set the service to `cryptowallet_cdp`, and enter:

   ```text
   project_id YOUR_PROJECT_ID
   api_key_id YOUR_API_KEY_ID
   api_key_secret YOUR_API_KEY_SECRET
   wallet_secret YOUR_WALLET_SECRET
   ```

6. Reload the cog so CryptoWallet initializes its server-only JWT identity key.
7. Configure the browser client and custom authentication using the settings below.
8. Run `[p]walletset cdpstatus`, `[p]walletset cdpcheck`, and `[p]walletset jwtstatus`, then test
   `[p]wallet` and `[p]wallet authorize` using valueless testnet assets only.

Basic wallet provisioning and public-address display use the server credentials. Protected
authorization, recovery, export, and transaction approval also require the browser/custom-auth
configuration below.

#### Browser client and custom authentication

In the same CDP project, configure the non-custodial wallet browser client and custom
authentication to match the deployed companion:

| CDP setting | Required value |
| --- | --- |
| Allowed domain/origin | The exact website origin, such as `https://your-site.example`. Include a non-default port when used, but do not include `/cryptowallet` or another URL path. |
| JWKS URL | `https://your-site.example/cryptowallet/api/jwks.php` |
| JWT issuer (`iss`) | The exact configured CryptoWallet approval URL, such as `https://your-site.example/cryptowallet` |
| JWT audience (`aud`) | The CDP `project_id` UUID |
| JWT algorithm | `ES256` |
| User identifier claim | `sub` |

`[p]walletset jwtstatus` displays these public values and never displays the JWT private key.
CryptoWallet uses the stable wallet-profile ID as `sub`. Run `[p]walletset jwksfile` when a
manually deployed public `jwks.json` is needed; the PHP JWKS endpoint otherwise publishes the
public key installed by the secure website setup. Neither form contains a private key or CDP
credential. The browser allowed-domain entry and the server API key IP allowlist are separate:
the former contains the website origin, while the latter contains the server’s public outbound IP.

Authorization handoffs expire after three minutes. They are sent by DM, carried after `#handoff=` so they
are not sent to the web server, and removed from browser history as soon as the page loads. The
static page authenticates the handoff directly with CDP and grants one user-scoped delegation across all wallet accounts only after the user presses the confirmation button. No website-to-bot listener is required.

Public EVM history reads the reusable Red shared API-token namespace `etherscan` with
field `api_key`. Configure it with `[p]set api etherscan api_key,<key>` in a private bot-owner
context; CryptoWallet never displays the value. One Etherscan V2 key can be reused by other cogs.

The wallet provider reads its separate values from Red's shared API-token namespace `cryptowallet_cdp`.
Provision them only through Red's bot-owner API-token modal or another approved server-side
secret mechanism; there is intentionally no CryptoWallet command that accepts or displays them.

`[p]walletset cdpstatus` reports only whether configuration is complete and the names of any
missing fields. Provisioning uses a small authenticated CDP v2 HTTP client, deterministic
idempotency keys, and spend permissions disabled. The client uses Red's compatible `aiohttp`
version instead of installing Coinbase's Python SDK, whose dependency requirements conflict with
Red-DiscordBot 3.5. It stores only the resulting CDP user ID and public smart-account address.

Intentionally excluded:

- wallet deletion, provider-account deletion, signer ejection, and automatic full-balance migration

Remaining work includes:

- broader high-risk policy/2FA enforcement
- reconciliation of local usage estimates with an authoritative CDP billing-usage API, if Coinbase exposes one
- mainnet support

## Module layout

```text
cryptowallet/
├── __init__.py
├── cryptowallet.py     # Thin Red cog composition and lifecycle
├── commands/
│   ├── __init__.py
│   ├── user.py         # Small user-command composition layer
│   ├── account.py      # Protected signer-backup command
│   ├── core.py         # Wallet summary, balance, settings, and cooldowns
│   ├── authorization.py # Signing authorization lifecycle
│   ├── transactions.py # Send intents, approval, and intent status
│   ├── activity.py     # Blockchain history and public TXID lookup
│   ├── admin.py        # Bot-owner configuration and diagnostics
│   ├── constants.py    # Shared command limits and cooldowns
│   └── views.py        # Owner-bound Discord buttons and pagination
├── backend/
│   ├── __init__.py
│   ├── auth.py         # ES256 key lifecycle, JWKS, and custom-auth JWTs
│   ├── config.py       # Config registration and stored-data helpers
│   ├── confirmation.py # Persistent global confirmation scheduler
│   ├── provisioning.py # Idempotent automatic wallet provisioning
│   └── usage.py        # CDP traffic limits, accounting, and owner warnings
├── core/
│   ├── __init__.py
│   ├── models.py       # Profiles, accounts, and transaction intents
│   ├── networks.py     # Supported chain metadata
│   └── validation.py   # Address and amount validation
├── providers/
│   ├── __init__.py
│   ├── base.py           # Provider interface
│   ├── cdp.py            # Server-only CDP configuration and provider boundary
│   └── cdp_api.py        # Minimal authenticated CDP v2 HTTP client
├── tests/
│   └── test_authorization.py # Authorization UI, renewal, and handoff regression tests
├── web/
│   ├── index.html
│   ├── recovery.html
│   ├── recovery.js
│   ├── security.html
│   ├── session.html
│   ├── app.js
│   ├── cdp-wallet.js     # Generated, self-hosted Coinbase SDK bundle
│   ├── styles.css
│   ├── package.json      # Pinned frontend dependencies and bundle command
│   ├── package-lock.json
│   ├── src/              # Auditable browser SDK integration source
│   ├── api/              # One-time relay, result callback, and public JWKS endpoints
│   └── server/           # Access-denied PHP/MySQL relay configuration
└── info.json
```

## Base Sepolia threat model

Protected assets are user testnet funds, signer ownership, the immutable Discord-to-CDP wallet mapping, CDP credentials, the deployment JWT key, limited signing delegations, and short-lived handoff tokens. The bot host and server-side secret stores are trusted; Discord accounts, Discord channels and DMs, browser assets, public RPC endpoints, explorer data, and all user input are treated as potentially compromised. CDP is currently trusted to preserve embedded-wallet identities, secure signer material, and enforce time-limited user-scoped delegation.

| Threat | Enforced control | Residual risk and response |
| --- | --- | --- |
| Compromised Discord account | Persistent emergency lock, owner-only unlock, pending-intent rejection, and attempted delegation revocation | A signed handoff already delivered by DM may remain usable until its three-minute expiry; lock and retry revocation, then independently verify identity |
| Duplicate clicks or delayed provider response | Atomic intent claim, deterministic idempotency key, explicit uncertain state, and no automatic resubmission | Bot owner must reconcile an uncertain intent before permitting a replacement |
| CDP or RPC outage | Fail closed before submission; persist submitted or uncertain state and use jittered confirmation backoff | Status and revocation may remain temporarily unconfirmed |
| Bot restart during submission | Persist processing before the provider call and convert interrupted processing to uncertain on restart | Manual reconciliation is required when no operation hash was returned |
| Wrong user, deployment, application, project, profile, purpose, or account | Signed bound claims, exact stored-profile checks, address normalization, CDP user and account verification, and owner-bound Discord controls | Authorization remains expiry-bounded; recovery adds atomic one-time relay consumption |
| Browser or public website compromise | No CDP secret, JWT private key, signer key, or raw private key is available to site JavaScript; export uses the Coinbase isolated iframe | A malicious page could mislead users, so deployment integrity and HTTPS remain operational requirements |
| Bot-host or CDP credential compromise | Profile-wide policy-limited testnet delegation, owner pause, per-wallet lock, usage warnings, capability allowlists, configurable transaction ceilings, and testnet-only enforcement | A fully compromised trusted backend or provider remains outside what Discord confirmation alone can contain; rotate credentials, pause processing, lock wallets, and revoke delegations |
| Destructive account action | No wallet deletion, provider-account deletion, signer ejection, or automatic balance migration command exists | Users deliberately transfer funds and may separately export their signer or revoke bot authorization |

### Adversarial acceptance checklist

Automated coverage verifies every security-sensitive approval field changes both the displayed quote and its approval fingerprint; duplicate approval after an ambiguous provider failure cannot resubmit; recovery handoffs reject wrong-purpose, wrong-user, expired, and replayed use; provider/public RPC receipt disagreement becomes uncertain; malformed and expired JWTs, wrong project audience, unsupported purpose creation, mismatched profile, provider, and Discord identity, missing or invalid accounts, foreign deployment and application rejection, emergency-lock authorization blocking, and uncertain-submission persistence all fail closed. The combined Base Sepolia test must still verify:

- lock immediately after creating a pending intent, then confirm its old approval button cannot submit;
- lock with active delegation, verify revocation, and confirm only a bot owner can unlock;
- simulate CDP failure before and after the atomic submission boundary without creating a duplicate transfer;
- restart with processing and submitted intents and verify uncertain conversion versus normal confirmation recovery;
- verify a recovery token cannot be mistaken for authorization by the packaged UI;
- confirm secrets and bearer values never appear in channels, logs, server URLs, Git, or public browser assets;
- confirm wallet address, deposits, balances, history, TXID lookup, and revocation remain usable under the documented lock and pause rules.

### One-time handoff boundary

Recovery handoffs are registered by the bot over authenticated outbound HTTPS and stored encrypted
at rest by the public relay. The DM URL carries an opaque random handle that is consumed atomically
once; replay, expiry, and unknown handles return the same unavailable response. Authorization still
uses a direct three-minute signed handoff and must not be described as single-use. Normal wallet
provisioning, reads, and authorized sends do not depend on the relay.
The packaged `/setup/` page can initialize the MySQL/MariaDB relay tables and private server-side
configuration on DirectAdmin-style hosting. For an existing installation, the same page uses the
private configuration to apply only bundled database updates and displays no connection or migration
details. The original relay secret and configuration remain unchanged during updates.

## Remaining work

Frontend build requirements: Node.js 20.18+ and npm. Run `npm ci` in
`cryptowallet/web/`, then use `npm run build` for `src/cdp-wallet.js`,
`npm run build:clanker` for `src/clanker-external.js`, or `npm run build:security`
for `src/security.js`. Deploy the corresponding generated root-level browser bundle with
the other public assets. Never deploy `node_modules/` or the development-only `src/` directory.

1. Complete the combined Discord acceptance pass for Base Sepolia and Solana devnet.
2. Verify the deployed dual-account recovery page with both Coinbase isolated export controls.
3. Live-check non-owner cooldown wording with a second Discord account; automated enforcement and
   owner/administrator exemption coverage passes in the representative Red environment.
4. Deploy the versioned relay migrations, then live-verify TOTP enrollment, invalid and replayed-code
   rejection, protected sends, disable, replacement, and locked lost-factor recovery.
5. Complete independent technical review before release to `main`; obtain jurisdiction-specific legal review before monetization or expanded financial services.

## Security boundary

Until those milestones are complete:

- Use Base Sepolia and valueless test assets only.
- Never store or request passwords, OTPs, private keys, or recovery phrases in Discord or MyBB.
- Never expose CDP credentials or authorization signing keys to the browser.
- Never grant the bot unrestricted withdrawal authority.
- Do not pool user funds.
- Do not expose wallet deletion, provider-account deletion, signer ejection, or automatic full-balance migration commands.
- Keep each user’s wallet profile and public deposit address intact even if authorization is revoked or the user stops using the bot.
- Do not treat Discord commands or OAuth identity verification as blockchain signatures.
- Keep trading logic separate from wallet ownership and signing logic.
- Do not enable a real-asset network on a production bot or submit a real transaction without explicit maintainer authorization.
