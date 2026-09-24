# Crypto Wallet

Crypto Wallet is an experimental, Base-first wallet cog for Red-DiscordBot. Its intended user experience is bot-first: a user's wallet is provisioned automatically when they first interact with the wallet commands, and the bot immediately returns a public deposit address.

The secure Base prototype is tracked in issue #35; multi-network expansion is tracked separately in issue #39. All enabled networks remain testnets and must not be used with real assets.

## Multi-network safety boundary

CryptoWallet models each blockchain with an explicit chain family, network reference, native-token precision, testnet state, and independently reviewed capabilities for balances, sends, history, transaction lookup, delegation, recovery, export, and fee sponsorship. EVM chain IDs and Solana cluster names are deliberately different fields.

A capability must be enabled in both the network registry and the active provider adapter before a send can be created. Address validation is dispatched from the explicitly selected network, including independent 32-byte base58 validation for Solana addresses; a Solana address is never interpreted as EVM data. Transaction storage now also exposes network-neutral atomic amount and fee fields while retaining the existing Base wei keys for stored-profile compatibility.

Base Sepolia and Solana devnet are the only send-enabled networks. Ethereum Sepolia, Arbitrum Sepolia, Polygon Amoy, and Avalanche Fuji are enabled only for their reviewed read-only capabilities. Solana devnet has a distinct CDP Solana account with native SOL balance, recent activity, transaction-signature lookup, explorer support, protected native-SOL sends, and isolated Coinbase key export. Solana tokens remain disabled.

Base Mainnet, Polygon Mainnet, OP Mainnet, BNB Chain, and Zora are staged as disabled metadata-only definitions. Base Mainnet is identified explicitly as chain ID 8453, but it has zero wallet capabilities and cannot be selected through activity or send commands. The existing `base` command alias continues to mean Base Sepolia. Polygon Mainnet is reserved for a separately reviewed future Polymarket CLOB V2 handoff (chain ID 137, pUSD collateral); it has no enabled wallet capabilities today. The other entries use CDP's documented network identifiers. They have no enabled balance, discovery, history, send, delegation, or sponsorship capability and do not appear in ordinary wallet cards. Future mainnet sends must use user-funded native gas; this project does not promise bot-funded gas. Enabling any of these entries still requires the separate mainnet security, legal, policy, fee-estimation, and transaction-verification review. The shared Polymarket handoff contract is likewise metadata-only: Polygon chain 137 and pUSD are named so a later adapter has an explicit target, but it is disabled and cannot provision accounts, read balances, approve collateral, or sign a transaction.

Bot owners can inspect this boundary with `[p]walletset mainnet status` and force it closed with `[p]walletset mainnet pause`. Individual controls use `[p]walletset mainnet capability <name> <status|disable|enable>`; disabling is immediate, while enabling requires both the exact permanent-loss acknowledgement and an already reviewed code-level capability. Set all three native-ETH ceilings atomically with `[p]walletset mainnet limits <per-transaction> <per-user-day> <installation-day>`; the command requires transaction ≤ user-day ≤ installation-day and does not enable mainnet. Future Base-mainnet submissions must reserve daily capacity by immutable intent ID in persistent Red configuration before provider submission; duplicate reservations remain idempotent across restarts, while changed intent data fails closed. Any future real-value approval card must also bind and display the Base chain ID, amount, destination, recipient count, gas estimate and maximum, gas payer, existing authorization requirement, irreversibility, and permanent-loss warning; a missing or inconsistent fee maximum blocks approval. Discord confirmation approves only the immutable displayed intent and does not require a second companion visit. Optional step-up verification is being built with standard RFC 6238 TOTP codes compatible with Authy and other authenticator apps; its validation foundation uses a bounded clock window and rejects reuse of an accepted time counter. This does not use the legacy Authy API or alter the send flow yet. Pending and enabled enrollment state now stores only AES-256-GCM ciphertext in Red configuration; its separate server-only encryption key remains in Red shared API tokens, and authenticated encryption binds the secret to this deployment, Discord user, and immutable wallet profile. For opted-in accounts, pressing Approve opens a private Discord modal for a six-digit code; successful verification is bound to the exact immutable transaction fingerprint and is consumed only after quote refresh and final balance checks pass. Accounts without TOTP retain the existing approval flow. The guarded enable command requires the exact permanent-loss acknowledgement and still refuses activation while Base Mainnet has no reviewed code-level capabilities. A stored setting alone cannot enable mainnet.

Ethereum Sepolia smart-account operations cannot assume Base gas sponsorship. CDP's built-in Paymaster supports Base networks; Ethereum Sepolia must use user-funded test ETH or a separately reviewed compatible paymaster.

See [Polygon / Polymarket handoff boundary](docs/polygon-polymarket-handoff.md) for the develop-only provider-neutral execution decision record.

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
these limits. History remains compact at 10 entries per page, protects new page requests from
rapid repeat clicks, and includes a permanent BaseScan address link for complete public history.
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
[p]wallet mode [testnet|live]     # Live remains disabled
[p]wallet notifications [true|false]
[p]wallet security              # Show emergency-lock status
[p]wallet security lock         # Alias: freeze; only bot owner can unlock
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
[p]walletset lock <mention-or-user-id>      # Alias: freeze
[p]walletset unlock <mention-or-user-id>    # Alias: unfreeze
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

`wallet security lock` immediately persists an emergency lock, rejects pending send intents, and attempts to revoke the profile-wide bot signing delegation. While locked, receiving funds, balances, history, public transaction lookup, and authorization revocation remain available; new sends, approval clicks, authorization/renewal links, and signer export are blocked. Only the configured Red bot owner can remove the lock with `walletset unlock <mention-or-user-id>` after an independent identity review. An already-issued signed handoff can remain usable until its three-minute expiry, so the owner should retry delegation revocation if CDP was unavailable during locking. This is the current compromised-Discord response; a Discord-only PIN would not be an independent factor, and optional external 2FA remains future work.

`wallet recovery` DMs a three-minute, purpose-bound, single-use link for backing up the user’s wallet signer. The URL contains only a random opaque handle. The public relay atomically consumes it and releases the encrypted-at-rest CDP handoff to the browser, where it is removed from browser history immediately. The browser validates the expected CDP user and account addresses and opens CDP’s isolated secure key-export iframe. The private key is copied within Coinbase’s iframe and is never exposed to the site JavaScript, Discord, or the bot. The smart account itself has no exportable private key; exporting its wallet signer EOA does not move funds or delete the provider account.

`wallet send` accepts either a network-valid address or a current non-bot server-member mention,
then creates a 15-minute preview with owner-bound **Approve** and **Reject** buttons. Mentioned
recipients are resolved to the account family for the explicitly selected network.
Base Sepolia sends use CDP-sponsored smart-account operations and display a zero user-paid gas
fee. Solana devnet sends label the current cost as a network fee (EVM cards retain the technically
distinct gas-fee label) and submit a strict native System Program transfer. Before either
transaction is accepted as confirmed, its public-chain result must match
the stored sender, recipient, and exact atomic amount. Ethereum Sepolia and the additional EVM
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
public history. `wallet txid <network> <txid-or-signature>` performs an explicit-network public
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

The provider reads these values from Red's shared API-token namespace `cryptowallet_cdp`.
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

Automated coverage verifies malformed and expired JWTs, wrong project audience, unsupported purpose creation, mismatched profile, provider, and Discord identity, missing or invalid accounts, expired handoffs, wrong-user relay consumption, replay rejection, foreign deployment and application rejection, emergency-lock authorization blocking, and uncertain-submission persistence. The combined Base Sepolia test must still verify:

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
The packaged `/setup/` wizard can initialize the MySQL/MariaDB relay tables and private server-side
configuration on DirectAdmin-style hosting. It stays available until installation succeeds, then
writes a private lock file and refuses reuse. Deploy it only when you are ready to complete setup.

## Remaining work

Frontend build requirements: Node.js 20.18+ and npm. Run `npm ci && npm run build` in
`cryptowallet/web/` whenever the pinned frontend dependencies or `src/cdp-wallet.js` change.
Deploy the generated `cdp-wallet.js` with the other public assets. Never deploy `node_modules/`.

1. Complete the combined Discord acceptance pass for Base Sepolia and Solana devnet.
2. Verify the deployed dual-account recovery page with both Coinbase isolated export controls.
3. Live-check non-owner cooldown wording with a second Discord account; automated enforcement and
   owner/administrator exemption coverage passes in the representative Red environment.
4. Decide whether optional independent 2FA/risk policies and a server-consumed private relay are required for a later release.
5. Complete security and jurisdiction-specific legal review before considering any mainnet path.

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
- Do not enable Base mainnet, Ethereum mainnet, Solana mainnet, or any other real-asset network.
