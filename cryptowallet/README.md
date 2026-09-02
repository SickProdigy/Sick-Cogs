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

The browser interface is not an enrollment requirement. It is an independent account-control surface for sensitive operations:

- claiming control of an automatically provisioned wallet
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
[p]wallet balance
[p]wallet networks
[p]wallet send <address> <amount>
[p]wallet transaction <intent-id>
[p]wallet claim
```

Owner commands:

```text
[p]walletset view
[p]walletset cdpstatus
[p]walletset jwtstatus
[p]walletset approvalurl https://sickgaming.net/cryptowallet
[p]walletset clearapprovalurl
[p]walletset pair
[p]walletset paircancel
[p]walletset pairstatus
[p]walletset unpair
[p]walletset companion start [port]
[p]walletset companion stop
```

The first `wallet`, `wallet claim`, or `wallet send` command provisions the user's CDP end
user and Base Sepolia smart account if no stored profile exists. `wallet claim` verifies the same
Discord identity and records a claim only after CDP independently validates browser wallet control.

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
21. Public JWKS publication through the authenticated website-server bridge.
22. Five-minute, issuer-, audience-, deployment-, application-, purpose-, and user-bound JWTs.
23. Browser-session-protected PHP token proxy and owner-visible public JWT configuration.
24. Pinned, self-hosted Coinbase browser SDK bundle using custom authentication.
25. CDP access-token validation in the cog with exact provider-user and smart-account matching.
26. Persisted claim completion only after Discord, website installation, browser session, CDP user,
    and provisioned address all agree.

### CDP and custom-auth configuration

Create or select a project in the [Coinbase Developer Platform portal](https://portal.cdp.coinbase.com/).
The four `cryptowallet_cdp` fields do not all come from the same screen:

| Red field | Meaning | Where it comes from | Secret? |
| --- | --- | --- | --- |
| `project_id` | Public identifier for the selected CDP project. The browser SDK will use it to select the same project as the cog. | The selected project's settings or Embedded Wallet configuration in the CDP Portal. | No |
| `api_key_id` | Identifier for a CDP Secret API Key used by the cog for authenticated server requests. | CDP Portal → API Keys → Secret API Keys → Create API key. Copy the displayed API key ID. | Treat as sensitive metadata |
| `api_key_secret` | Private half of that Secret API Key. It signs short-lived CDP API authentication tokens. | Shown once with the newly created Secret API Key. Save it when Coinbase displays it. | Yes |
| `wallet_secret` | Separate wallet-authentication secret used for sensitive wallet creation and signing operations. It is not the API key secret. | Generate it from the selected project's Server Wallet/Wallet Secret page. Save it when Coinbase displays it. | Yes |

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
4. Configure the companion HTTPS URL and reload the cog. CryptoWallet generates its signing key.
5. Deploy `web/api/jwks.php`, then run `[p]walletset jwtstatus` to obtain the HTTPS JWKS URL,
   expected issuer, audience, and generated key ID.
6. Configure that JWKS URL, issuer, audience, and default
   `sub` user identifier under the project's CDP custom-auth settings.
7. As the Red bot owner, run `[p]set api`, set the service to `cryptowallet_cdp`, and enter:

   ```text
   project_id YOUR_PROJECT_ID
   api_key_id YOUR_API_KEY_ID
   api_key_secret YOUR_API_KEY_SECRET
   wallet_secret YOUR_WALLET_SECRET
   ```

8. Run `[p]walletset cdpstatus`, then test `[p]wallet` using Base Sepolia only.

`[p]walletset jwtstatus` displays only public configuration. It never displays the JWT private
key. The issuer is the configured companion URL, the audience is the CDP project ID, and tokens
use the stable wallet-profile ID as `sub`. Tokens last no more than five minutes and require a
paired website request plus a verified, matching browser session.

The provider reads these values from Red's shared API-token namespace `cryptowallet_cdp`.
Provision them only through Red's bot-owner API-token modal or another approved server-side
secret mechanism; there is intentionally no CryptoWallet command that accepts or displays them.

`[p]walletset cdpstatus` reports only whether configuration is complete and the names of any
missing fields. Provisioning uses Coinbase's official Python SDK, deterministic idempotency keys,
and spend permissions disabled. It stores only the resulting CDP user ID and public smart-account
address.

Not implemented:

- wallet recovery or export
- blockchain signing or broadcasting
- application delegation or policy enforcement
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
│   └── cdp.py            # Server-only CDP configuration and provider boundary
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

1. Deploy the claim assets and add the exact website origin to CDP's domain allowlist.
2. Test custom-auth configuration and the complete Base Sepolia claim path end to end.
3. Adapt the backend connection for the SickGaming private/restricted two-server deployment.
4. Test pairing, signatures, browser sessions, replay rejection, rotation, and unpairing.
5. Convert verified identity into recovery and account-security operations.
6. Connect unsigned intents to explicit browser signing.
7. Add optional, policy-limited bot delegation and independent revocation.
8. Verify key export, signer replacement, recovery, and migration away from CDP.
9. Test expired/replayed links, wrong-user OAuth, compromised Discord, provider outages, lost
   factors, linked identities, signing-key failure, and mismatched CDP users/addresses.
10. Complete security, threat-model, and jurisdiction-specific legal review before mainnet.

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
