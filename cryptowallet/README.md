# Crypto Wallet

Crypto Wallet is an experimental, Base-first wallet cog for Red-DiscordBot. Its intended user experience is bot-first: a user's wallet is provisioned automatically when they first interact with the wallet commands, and the bot immediately returns a public deposit address.

The project is tracked in issue #35. It is currently a Base Sepolia prototype and must not be used with real assets.

## Intended experience

```text
User runs the wallet command for the first time
→ Service creates an internal wallet profile
→ Wallet provider creates a user-associated EVM signer and smart account
→ Bot immediately displays the Base address
→ Wallet can receive deposits
```

Routine account information remains available through Discord:

- public wallet address
- selected network and chain ID
- balance and deposit information
- transaction-intent creation and status
- public transaction hashes and confirmations

Provider-backed wallet summaries are limited to one request per user every 10 seconds, and new
transaction-history cards to one every 15 seconds. Bot owners and server administrators bypass
these limits. History remains compact at 10 entries per page, protects new page requests from
rapid repeat clicks, and includes a permanent BaseScan address link for complete public history.
The plural `wallets` command is accepted as an alias for `wallet`.

The browser interface is not an enrollment requirement. It is an independent account-control surface for sensitive operations:

- authorizing limited bot actions for an automatically provisioned wallet
- recovery and backup configuration
- signer or key export where supported
- provider migration
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

The initial release requires explicit user authorization. Delegated execution will only be added after CDP policy enforcement, user-controlled revocation, and the complete exit path have been tested.

## Custody and identity

The target is a standards-based EVM smart account controlled by a user-owned, exportable or replaceable signer. Coinbase Developer Platform (CDP) is the provisional wallet provider, but provider-specific behavior must remain behind an internal adapter.

SickGaming maintains its own wallet-profile identifier. External identities are verified links rather than primary keys:

```text
Wallet profile
├── CDP end-user identity
├── Discord immutable user ID
├── MyBB immutable user ID
├── optional Telegram immutable user ID
├── EVM owner signer
└── Base smart account
```

Never merge accounts by username, display name, supplied platform ID, or wallet address alone.

## Companion site

The packaged browser assets live in [`web/`](web/) and are intended to be published at:

```text
https://sickgaming.net/cryptowallet
```

The cog provides the authoritative backend through its `aiohttp` listener in `companion.py`. The
separately hosted companion website uses the assets under `web/` and communicates with that cog
backend. The website and cog are two components; there is no separate companion service.

The listener currently defaults to loopback, which only works when the reverse proxy and bot share
a host. SickGaming runs its website and bot on different servers, so the listener must not be made
public merely to connect them. The next milestone is an authenticated private/restricted
website-server-to-cog connection with one-time pairing, durable credential rotation, and
revocation.

Static files can be served by the companion, the SickGaming web server, or a future MyBB plugin. Static files cannot safely contain or replace server-side functionality for:

- Discord OAuth callbacks
- custom-auth JWT signing and JWKS publication
- CDP API authentication
- wallet recovery state
- signer export
- delegated-authority changes

No API key, OAuth client secret, wallet secret, signing key, or private user material may be embedded in `web/`.

### Initial companion API v1

The current implementation establishes the browser-session and response contract. When a reverse
proxy can securely reach the cog, it preserves the public `/cryptowallet` prefix while forwarding
it to the listener with that prefix removed. The protected flow is:

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

This is not yet the finished SickGaming two-server contract. Before the web server can call these
routes, it must pair with the cog and authenticate server-to-server requests. Until that exists,
keep the listener on loopback or an explicitly restricted private test network; do not expose it
to the public internet.

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
[p]wallet send <address> <amount>
[p]wallet intent <bot-reference>
[p]wallet txid <txid>
[p]wallet transactions           # Aliases: tx, trans, history
[p]wallet notifications [true|false]
[p]wallet authorize
[p]wallet auth                  # Short alias
[p]wallet authorization
[p]wallet revoke
```

Owner commands:

```text
[p]walletset view
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

The first `wallet`, `wallet authorize`, or `wallet send` command provisions the user's CDP end
user and Base Sepolia smart account if no stored profile exists. `wallet authorize` sends a three-minute
handoff URL by DM. The URL token stays in the fragment, is removed from browser history immediately,
and is validated by CDP custom authentication before the browser can grant a 24-hour delegation for
the exact provisioned Base Sepolia smart account. `wallet revoke` requires an owner-bound
Discord confirmation, revokes only that account-scoped delegation, and verifies with CDP that it
is inactive; it does not delete the wallet or move funds.

`wallet send` creates a 15-minute preview with owner-bound **Approve** and **Reject** buttons.
Because CDP sponsors Base Sepolia smart-account user operations, the displayed user gas fee is
`0 ETH (sponsored by CDP)` and the estimated total equals the transfer amount. Approval checks
CDP's authoritative account-delegation status. When authorization is absent, the bot DMs the
short-lived authorization link and leaves the intent pending for another approval after completion.
When authorization is active, the approval view is checked against the exact quote originally
displayed. The provider then rebuilds the sponsored Base Sepolia quote immediately before signing;
any material change updates the preview and requires another approval. An unchanged quote atomically moves the intent into processing, rechecks its balance and immutable
fields, and submits a sponsored Base Sepolia smart-account user operation
with a stable CDP idempotency key. The bot stores the public user-operation hash, transaction hash,
provider status, and block number when returned. It then polls the same operation without
resubmitting it and sends the owner a separate user-only confirmation containing the explorer
link. The original approval card progresses from pending to submitted, then updates with the final
status, transaction hash, and block number when confirmation arrives. Submitted operations
can also be refreshed on demand through `wallet intent <bot-reference>`.
`wallet transactions` (or `wallet tx`/`wallet trans`) provides owner-bound, ten-at-a-time
pagination over the wallet's indexed incoming and outgoing Base Sepolia activity, including
native, ERC-20, and ERC-721 interactions. `wallet txid <txid>` independently retrieves a public
transaction, receipt, and smart-account internal-transfer data directly from Base Sepolia; it
does not expose the bot's private intent metadata.

## Current implementation status

Completed:

1. Provider-neutral cog foundation.
2. Wallet profile, public account, transaction intent, and provider models.
3. Base Sepolia-only network configuration.
4. Exact ETH-to-wei and EVM address validation.
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
16. Idempotent, deployment-scoped CDP end-user and Base Sepolia smart-account provisioning.
17. Per-user concurrency control and public profile persistence after successful provisioning.
18. Read-only Base Sepolia native ETH balance lookup with bounded pagination.
19. Explorer-linked address and balance display in `wallet` and `wallet balance`.
20. Automatically generated, server-only P-256 custom-auth signing key with stable JWK thumbprint.
21. Owner-only public JWKS export and a static PHP JWKS endpoint with no bot connection.
22. Three-minute, issuer-, audience-, deployment-, application-, purpose-, user-, and address-bound
    authorization handoff JWTs delivered only by DM and carried in the URL fragment.
23. Pinned, self-hosted Coinbase browser SDK bundle using custom authentication.
24. Exact CDP user and smart-account matching before account-scoped delegation.
25. Explicit browser creation of a 24-hour delegation for only the provisioned account.
26. Atomic, idempotent Base Sepolia smart-account submission checkpoint in version `0.16.0`.
27. Minimal authenticated CDP v2 HTTP integration using Red's existing `aiohttp` stack, avoiding
    the official Python SDK's incompatible networking dependency upgrades.
28. Submitted-operation reconciliation with bounded automatic polling, user-only confirmation
    notices, persistent transaction hashes, and on-demand refresh after a cog restart.

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
6. Run `[p]walletset cdpstatus`, then test `[p]wallet` using Base Sepolia only.

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
static page authenticates the handoff directly with CDP and grants an account-scoped delegation
only after the user presses the confirmation button. No website-to-bot listener is required.

The provider reads these values from Red's shared API-token namespace `cryptowallet_cdp`.
Provision them only through Red's bot-owner API-token modal or another approved server-side
secret mechanism; there is intentionally no CryptoWallet command that accepts or displays them.

`[p]walletset cdpstatus` reports only whether configuration is complete and the names of any
missing fields. Provisioning uses a small authenticated CDP v2 HTTP client, deterministic
idempotency keys, and spend permissions disabled. The client uses Red's compatible `aiohttp`
version instead of installing Coinbase's Python SDK, whose dependency requirements conflict with
Red-DiscordBot 3.5. It stores only the resulting CDP user ID and public smart-account address.

Not implemented:

- wallet recovery or export
- independent delegation revocation and broader policy enforcement
- restart-time background resumption for submitted-operation polling (manual refresh is available)
- mainnet support

## Module layout

```text
cryptowallet/
├── __init__.py
├── cryptowallet.py       # Thin Red cog and lifecycle
├── commands.py           # User wallet commands
├── admin.py              # Owner configuration commands
├── config.py             # Config registration and stored-data helpers
├── models.py             # Profiles, accounts, intents, and approval sessions
├── networks.py           # Supported chain metadata
├── validation.py         # Address and amount validation
├── provisioning.py       # Idempotent automatic wallet provisioning
├── jwt_auth.py           # ES256 key lifecycle, JWKS, and custom-auth JWTs
├── sessions.py           # One-time state and replay prevention
├── pairing.py            # Website-server pairing and credential lifecycle
├── companion.py          # HTTP routes, OAuth, and listener lifecycle
├── providers/
│   ├── __init__.py
│   ├── base.py           # Provider interface
│   ├── cdp.py            # Server-only CDP configuration and provider boundary
│   └── cdp_api.py        # Minimal authenticated CDP v2 HTTP client
├── web/
│   ├── index.html
│   ├── recovery.html
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

## Remaining work

Frontend build requirements: Node.js 20.18+ and npm. Run `npm ci && npm run build` in
`cryptowallet/web/` whenever the pinned frontend dependencies or `src/cdp-wallet.js` change.
Deploy the generated `cdp-wallet.js` with the other public assets. Never deploy `node_modules/`.

1. Deploy the rebuilt authorization assets and exported `jwks.json`, then test the Base Sepolia authorization path.
2. Add bot-side authoritative delegation status and independent revocation.
3. Add one-time handoff consumption if the static site gains trusted server-side state; until then,
   rely on the three-minute expiry and do not describe the link as single-use.
4. Convert verified identity into recovery and account-security operations.
5. Connect unsigned intents to policy-limited delegated signing and explicit Discord approval.
6. Verify key export, signer replacement, recovery, and migration away from CDP.
7. Test expired/replayed links, compromised Discord, provider outages, lost
   factors, linked identities, signing-key failure, and mismatched CDP users/addresses.
8. Complete security, threat-model, and jurisdiction-specific legal review before mainnet.

## Security boundary

Until those milestones are complete:

- Use Base Sepolia and valueless test assets only.
- Never store or request passwords, OTPs, private keys, or recovery phrases in Discord or MyBB.
- Never expose CDP credentials or authorization signing keys to the browser.
- Never grant the bot unrestricted withdrawal authority.
- Do not pool user funds.
- Do not treat Discord commands or OAuth identity verification as blockchain signatures.
- Keep trading logic separate from wallet ownership and signing logic.
- Do not enable Base mainnet, Ethereum mainnet, or Solana.
