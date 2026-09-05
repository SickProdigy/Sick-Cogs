# Crypto Wallet

Crypto Wallet is an experimental, Base-first wallet cog for Red-DiscordBot. Its intended user experience is bot-first: a user's wallet is provisioned automatically when they first interact with the wallet commands, and the bot immediately returns a public deposit address.

The secure Base prototype is tracked in issue #35; multi-network expansion is tracked separately in issue #39. All enabled networks remain testnets and must not be used with real assets.

## Multi-network safety boundary

CryptoWallet models each blockchain with an explicit chain family, network reference, native-token precision, testnet state, and independently reviewed capabilities for balances, sends, history, transaction lookup, delegation, recovery, export, and fee sponsorship. EVM chain IDs and Solana cluster names are deliberately different fields.

A capability must be enabled in both the network registry and the active provider adapter before a send can be created. Address validation is dispatched from the explicitly selected network, including independent 32-byte base58 validation for Solana addresses; a Solana address is never interpreted as EVM data. Transaction storage now also exposes network-neutral atomic amount and fee fields while retaining the existing Base wei keys for stored-profile compatibility.

Base Sepolia and Solana devnet are the only send-enabled networks. Ethereum Sepolia, Arbitrum Sepolia, Polygon Amoy, and Avalanche Fuji are enabled only for their reviewed read-only capabilities. Solana devnet has a distinct CDP Solana account with native SOL balance, recent activity, transaction-signature lookup, explorer support, protected native-SOL sends, and isolated Coinbase key export. Solana tokens remain disabled, and no mainnet is registered.

Ethereum Sepolia smart-account operations cannot assume Base gas sponsorship. CDP's built-in Paymaster supports Base networks; Ethereum Sepolia must use user-funded test ETH or a separately reviewed compatible paymaster.

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

The packaged browser assets live in [`web/`](web/) and are intended to be published at:

```text
https://sickgaming.net/cryptowallet
```

The supported authorization and key-export pages authenticate the bot's short-lived, signed
handoff directly with CDP. They do not require the optional cog listener, website pairing, or a
downloaded pairing credential. Ordinary provisioning and read-only commands do not require the
website at all.

`backend/companion.py`, the pairing commands, and the signed PHP relay remain packaged as dormant
infrastructure for a future server-consumed workflow. They are not part of routine deployment.
Keep the listener disabled or bound to loopback; never expose it publicly merely to connect
separate website and bot hosts.

Static files can be served by the companion, the SickGaming web server, or a future MyBB plugin. Static files cannot safely contain or replace server-side functionality for:

- Discord OAuth callbacks
- custom-auth JWT signing and JWKS publication
- CDP API authentication
- wallet recovery state
- signer export
- delegated-authority changes

No API key, OAuth client secret, wallet secret, signing key, or private user material may be embedded in `web/`.

### Deferred private companion API

The repository retains a versioned browser-session and response contract for future use. If that
workflow is deliberately enabled on a private or loopback deployment, the protected flow is:

```text
GET /cryptowallet/session/<one-time-token>
→ Discord OAuth
→ GET /cryptowallet/oauth/callback
→ one-time token is consumed
→ short-lived HttpOnly browser cookie is issued
→ redirect to /cryptowallet/session
→ browser calls /cryptowallet/api/session.php
→ PHP signs GET /api/v1/session to the cog backend and forwards the browser cookie
```

Cog endpoint `GET /api/v1/session` requires both a valid paired-server signature and the user's
browser cookie. The public PHP proxy returns its stable JSON envelope containing only
server-authoritative session data. Transaction fields are loaded from the stored intent; the
endpoint does not accept addresses, amounts, wallet identifiers, or authorization decisions from
browser input.

Success envelope:

```json
{"data": {"version": 1, "purpose": "claim", "expires_at": 0, "identity_verified": true, "wallet": {"address": "0x...", "claimed": false}, "cdp": {"project_id": "..."}, "transaction": null}}
```

Error envelope:

```json
{"error": {"code": "session_unavailable", "message": "The wallet session is missing, invalid, or expired."}}
```

The browser cookie is distinct from the OAuth state token, marked `Secure`, `HttpOnly`, and
`SameSite=Strict`, scoped to the configured companion path, and expires with the approval session.

This is not the active authorization/export path and is not yet the finished SickGaming two-server
contract. Keep it disabled unless the complete authenticated deployment is being tested.

### Website-server authentication v1

After pairing, the website server signs protected requests with the returned credential. It sends:

```text
X-SickWallet-Installation: <installation-id>
X-SickWallet-Timestamp: <unix-seconds>
X-SickWallet-Nonce: <unique-random-value>
X-SickWallet-Signature: <lowercase-hex-hmac-sha256>
```

The signature key is the durable credential. Its canonical UTF-8 input is:

```text
v1\n<timestamp>\n<nonce>\n<METHOD>\n<backend-path>\n<sha256-body-hex>
```

The backend rejects unknown installations, invalid signatures, query strings, timestamps outside
the five-minute window, and reused nonces. It retains at most 500 recent nonces and clears them
when the website is unpaired. `GET /api/v1/server/status` is the first protected endpoint and can
be used by the website backend to confirm its stored credential.

This credential belongs only in the website server's secret storage. Frontend JavaScript must
never construct these headers or receive the credential.

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
[p]wallet intent <bot-reference>
[p]wallet txid <network> <txid-or-signature>
[p]wallet transactions [network] # Aliases: tx, trans, history
[p]wallet token [network]
[p]wallet token add <network> <contract>
[p]wallet mode [testnet|live]     # Live remains disabled
[p]wallet notifications [true|false]
[p]wallet security              # Show emergency-lock status
[p]wallet security lock         # Alias: freeze; only bot owner can unlock
[p]wallet authorize
[p]wallet auth                  # Short alias
[p]wallet authorization
[p]wallet recovery             # Aliases: recover, backup
[p]wallet revoke
```

Owner commands:

```text
[p]walletset view
[p]walletset usage
[p]walletset lock <mention-or-user-id>      # Alias: freeze
[p]walletset unlock <mention-or-user-id>    # Alias: unfreeze
[p]walletset pause
[p]walletset resume
[p]walletset sendlimit [network] [amount|clear]
[p]walletset delegationdays [1-365]
[p]walletset cdpstatus
[p]walletset cdpcheck
[p]walletset jwtstatus
[p]walletset jwksfile
[p]walletset approvalurl https://sickgaming.net/cryptowallet
[p]walletset clearapprovalurl
[p]walletset pair
[p]walletset paircancel
[p]walletset pairstatus
[p]walletset unpair
[p]walletset companion start [port]
[p]walletset companion stop
```

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
and verifies with CDP that it is inactive; it does not delete the wallet or move funds. When authorization is already active,
`wallet authorize` and `wallet authorization` show an explicit **Renew authorization** control.
Renewal sends a separately labeled protected link and leaves the current grant unchanged unless the
user deliberately completes that browser approval.

`wallet security lock` immediately persists an emergency lock, rejects pending send intents, and attempts to revoke the profile-wide bot signing delegation. While locked, receiving funds, balances, history, public transaction lookup, and authorization revocation remain available; new sends, approval clicks, authorization/renewal links, and signer export are blocked. Only the configured Red bot owner can remove the lock with `walletset unlock <mention-or-user-id>` after an independent identity review. An already-issued signed handoff can remain usable until its three-minute expiry, so the owner should retry delegation revocation if CDP was unavailable during locking. This is the current compromised-Discord response; a Discord-only PIN would not be an independent factor, and optional external 2FA remains future work.

`wallet recovery` DMs a three-minute, purpose-bound link for backing up the user’s wallet signer. The browser validates the expected CDP user and smart-account address, resolves its recorded wallet signer EOA, and opens CDP’s isolated secure key-export iframe. The private key is copied within Coinbase’s iframe and is never exposed to the site JavaScript, Discord, the bot, or the optional companion relay. The smart account itself has no exportable private key; exporting its wallet signer EOA does not move funds or delete the provider account. Importing the signer elsewhere may not automatically expose the smart-account balance, so users should transfer funds to an external address before leaving CDP unless the destination supports the existing smart account.

`wallet send` accepts either a network-valid address or a current non-bot server-member mention,
then creates a 15-minute preview with owner-bound **Approve** and **Reject** buttons. Mentioned
recipients are resolved to the account family for the explicitly selected network.
Base Sepolia sends use CDP-sponsored smart-account operations and display a zero user-paid gas
fee. Solana devnet sends show the current network fee and submit a strict native System Program
transfer. Before either transaction is accepted as confirmed, its public-chain result must match
the stored sender, recipient, and exact atomic amount. Ethereum Sepolia and the additional EVM
testnets remain read-only because no reviewed, complete pre-approval fee path is available.

Approval checks CDP's authoritative profile-wide delegation status. When authorization is absent,
the bot DMs a short-lived authorization link and leaves the intent pending for another approval.
An unchanged quote atomically moves the intent into processing and uses a stable provider
idempotency key. The persistent global processor begins confirmation after roughly 20–30 seconds,
applies jittered backoff, survives reloads, and never resubmits merely because status is delayed.
`wallet intent <bot-reference>` displays private bot-operation state.

`wallet transactions` without a network returns lightweight explorer links. Supplying `base`,
`eth`, or `sol` retrieves at most the latest ten supported activity records and links to complete
public history. `wallet txid <network> <txid-or-signature>` performs an explicit-network public
lookup and does not expose private intent metadata. Arbitrum Sepolia, Polygon Amoy, and Avalanche
Fuji support explicit TXID lookup but not indexed activity.

## Current implementation status

Completed:

1. Provider-neutral cog foundation.
2. Wallet profile, public account, transaction intent, and capability-aware provider models.
3. Explicit EVM/Solana chain-family metadata with capability-limited testnet registrations.
4. Network-dispatched address and native atomic-unit validation with legacy Base wei compatibility.
5. Expiring, user-scoped unsigned transaction intents.
6. Loopback companion listener and HTTPS public-URL configuration.
7. One-time state digests, expiration, replay prevention, and Discord OAuth identity matching.
8. Packaged wallet home, recovery, and security pages.
9. Deployment- and Discord-application-bound browser sessions.
10. Initial versioned, read-only companion session API with a separate HttpOnly browser token.
11. Atomic, single-use website-server pairing with revocable credentials in Red shared API tokens.
12. HMAC-authenticated website-server requests with timestamp and nonce replay protection.
13. PHP CLI pairing and status tools with server-only, atomic credential storage.
14. Signed PHP session proxy requiring paired-server authentication plus the user browser session.
15. Server-only CDP credential loading and readiness reporting without exposing secret values.
16. Idempotent, deployment-scoped CDP end-user, EVM smart-account, and Solana-account provisioning.
17. Per-user concurrency control and public profile persistence after successful provisioning.
18. Aggregated native and registered-token balance reads across enabled testnets.
19. Explorer-linked multi-network portfolio display in `wallet` and `wallet balance`.
20. Automatically generated, server-only P-256 custom-auth signing key with stable JWK thumbprint.
21. Owner-only public JWKS export and a static PHP JWKS endpoint with no bot connection.
22. Three-minute, issuer-, audience-, deployment-, application-, purpose-, user-, and address-bound
    authorization handoff JWTs delivered only by DM and carried in the URL fragment.
23. Pinned, self-hosted Coinbase browser SDK bundle using custom authentication.
24. Exact CDP user and signed account-set matching before user-scoped delegation.
25. Explicit browser creation of a policy-limited delegation for all provisioned accounts.
26. Atomic, idempotent Base Sepolia smart-account submission checkpoint in version `0.16.0`.
27. Minimal authenticated CDP v2 HTTP integration using Red's existing `aiohttp` stack, avoiding
    the official Python SDK's incompatible networking dependency upgrades.
28. Submitted-operation reconciliation with bounded automatic polling, user-only confirmation
    notices, persistent transaction hashes/signatures, and on-demand refresh after a cog restart.
29. Read-only Ethereum Sepolia, Arbitrum Sepolia, Polygon Amoy, and Avalanche Fuji capabilities.
30. Solana devnet balance, activity, lookup, protected native sends, confirmation, and key export.

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
consumer account key, Advanced Trade key, wallet private key, seed phrase, Discord token, or
companion pairing credential for any field above.

#### Required setup order

1. Create or select the CDP project and record its project ID.
2. Create a **Secret API Key** for that project and save its ID and secret.
3. Generate the project's **Wallet Secret** and save it separately.
4. As the Red bot owner, run `[p]set api`, set the service to `cryptowallet_cdp`, and enter:

   ```text
   project_id YOUR_PROJECT_ID
   api_key_id YOUR_API_KEY_ID
   api_key_secret YOUR_API_KEY_SECRET
   wallet_secret YOUR_WALLET_SECRET
   ```

5. Reload the cog so CryptoWallet initializes its server-only JWT identity key.
6. Run `[p]walletset cdpstatus`, then test `[p]wallet` using valueless testnet assets only.

The companion website and CDP custom-auth/JWKS configuration are not required to provision and
display a wallet address. Configure those later when implementing authorization, recovery,
export, or transaction-approval flows.

`[p]walletset jwtstatus` displays only public configuration. It never displays the JWT private
key. The issuer is the configured website URL, the audience is the CDP project ID, and tokens use
the stable wallet-profile ID as `sub`. Run `[p]walletset jwksfile`, upload the resulting public
`jwks.json` beside the wallet web files, and keep CDP's JWKS URL set to
`https://your-site.example/cryptowallet/api/jwks.php`. The file contains no private key or CDP
credential. Add the exact website origin to the CDP Client API Key domain allowlist.

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
│   ├── companion.py    # HTTP routes, OAuth, and listener lifecycle
│   ├── config.py       # Config registration and stored-data helpers
│   ├── confirmation.py # Persistent global confirmation scheduler
│   ├── pairing.py      # Website-server pairing and credential lifecycle
│   ├── provisioning.py # Idempotent automatic wallet provisioning
│   ├── sessions.py     # One-time state and replay prevention
│   └── usage.py        # CDP traffic limits, accounting, and owner warnings
├── core/
│   ├── __init__.py
│   ├── models.py       # Profiles, accounts, intents, and approval sessions
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
│   ├── api/              # Signed session/JWT/claim proxies and public JWKS endpoint
│   └── server/           # Deploy outside document root; PHP pairing/signing toolkit
└── info.json
```

## Base Sepolia threat model

Protected assets are user testnet funds, signer ownership, the immutable Discord-to-CDP wallet mapping, CDP and Discord OAuth credentials, the deployment JWT key, limited signing delegations, and short-lived handoff tokens. The bot host and server-side secret stores are trusted; Discord accounts, Discord channels and DMs, browser assets, public RPC endpoints, explorer data, and all user input are treated as potentially compromised. CDP is currently trusted to preserve embedded-wallet identities, secure signer material, and enforce time-limited user-scoped delegation.

| Threat | Enforced control | Residual risk and response |
| --- | --- | --- |
| Compromised Discord account | Persistent emergency lock, owner-only unlock, pending-intent rejection, and attempted delegation revocation | A signed handoff already delivered by DM may remain usable until its three-minute expiry; lock and retry revocation, then independently verify identity |
| Duplicate clicks or delayed provider response | Atomic intent claim, deterministic idempotency key, explicit uncertain state, and no automatic resubmission | Bot owner must reconcile an uncertain intent before permitting a replacement |
| CDP or RPC outage | Fail closed before submission; persist submitted or uncertain state and use jittered confirmation backoff | Status and revocation may remain temporarily unconfirmed |
| Bot restart during submission | Persist processing before the provider call and convert interrupted processing to uncertain on restart | Manual reconciliation is required when no operation hash was returned |
| Wrong user, deployment, application, project, profile, purpose, or account | Signed bound claims, exact stored-profile checks, address normalization, CDP user and account verification, and owner-bound Discord controls | Direct stateless handoffs are expiry-bounded, not server-consumed |
| Browser or public website compromise | No CDP secret, JWT private key, signer key, or raw private key is available to site JavaScript; export uses the Coinbase isolated iframe | A malicious page could mislead users, so deployment integrity and HTTPS remain operational requirements |
| Bot-host or CDP credential compromise | Profile-wide policy-limited testnet delegation, owner pause, per-wallet lock, usage warnings, capability allowlists, configurable transaction ceilings, and testnet-only enforcement | A fully compromised trusted backend or provider remains outside what Discord confirmation alone can contain; rotate credentials, pause processing, lock wallets, and revoke delegations |
| Destructive account action | No wallet deletion, provider-account deletion, signer ejection, or automatic balance migration command exists | Users deliberately transfer funds and may separately export their signer or revoke bot authorization |

### Adversarial acceptance checklist

Automated coverage verifies malformed and expired JWTs, wrong project audience, unsupported purpose creation, mismatched profile, provider, and Discord identity, missing or invalid accounts, unknown and expired stored sessions, wrong-user consumption, replay rejection in the private session layer, foreign deployment and application rejection, emergency-lock authorization blocking, and uncertain-submission persistence. The combined Base Sepolia test must still verify:

- lock immediately after creating a pending intent, then confirm its old approval button cannot submit;
- lock with active delegation, verify revocation, and confirm only a bot owner can unlock;
- simulate CDP failure before and after the atomic submission boundary without creating a duplicate transfer;
- restart with processing and submitted intents and verify uncertain conversion versus normal confirmation recovery;
- verify a recovery token cannot be mistaken for authorization by the packaged UI;
- confirm secrets and bearer values never appear in channels, logs, server URLs, Git, or public browser assets;
- confirm wallet address, deposits, balances, history, TXID lookup, and revocation remain usable under the documented lock and pause rules.

### One-time handoff boundary

The current direct custom-auth handoff is signed, purpose-bound, identity-bound, account-bound, and limited to three minutes, but it is not server-consumed and must not be described as single-use. The existing private session layer rejects replay after OAuth consumption, but it remains deferred infrastructure. Enabling it requires a complete authenticated two-server relay with durable credential rotation and deployment instructions; normal wallet provisioning, reads, and sends must not depend on manual listener or pairing steps.

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
