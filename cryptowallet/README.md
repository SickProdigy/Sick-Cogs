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

The cog provides a loopback-only `aiohttp` companion listener. A production web server must terminate HTTPS and reverse proxy the `/cryptowallet` routes to that listener. The listener must not be exposed directly to the internet.

Static files can be served by the companion, the SickGaming web server, or a future MyBB plugin. Static files cannot safely contain or replace server-side functionality for:

- Discord OAuth callbacks
- custom-auth JWT signing and JWKS publication
- CDP API authentication
- wallet recovery state
- signer export
- delegated-authority changes

No API key, OAuth client secret, wallet secret, signing key, or private user material may be embedded in `web/`.

## Current commands

User commands:

```text
[p]wallet
[p]wallet networks
[p]wallet send <address> <amount>
[p]wallet transaction <intent-id>
[p]wallet claim
```

Owner commands:

```text
[p]walletset view
[p]walletset approvalurl https://sickgaming.net/cryptowallet
[p]walletset clearapprovalurl
[p]walletset companion start [port]
[p]walletset companion stop
```

`wallet claim` currently verifies Discord identity but does not yet claim, export, or provision a CDP wallet.

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

Not implemented:

- CDP provider calls
- automatic wallet provisioning
- custom-auth JWT and JWKS support
- smart-account creation
- balance lookup
- wallet claiming, recovery, or export
- blockchain signing or broadcasting
- application delegation or policy enforcement
- mainnet support

## Planned module layout

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
├── sessions.py           # One-time state and replay prevention
├── companion.py          # HTTP routes, OAuth, and listener lifecycle
├── providers/
│   ├── __init__.py
│   ├── base.py           # Provider interface
│   └── cdp.py            # Coinbase CDP implementation
├── web/
│   ├── index.html
│   ├── recovery.html
│   ├── security.html
│   ├── app.js
│   └── styles.css
└── info.json
```

## Remaining work

1. Split the growing cog into the planned modules.
2. Define the companion API and reverse-proxy contract.
3. Implement secure CDP configuration and the provider adapter.
4. Automatically provision a CDP end user, owner signer, and Base Sepolia smart account on first wallet interaction.
5. Display the address and balance through Discord.
6. Convert the current identity-verification flow into wallet claiming and account security.
7. Add custom-auth JWT and JWKS integration using stable wallet-profile subjects.
8. Connect unsigned intents to explicit browser signing.
9. Add optional, policy-limited bot delegation and independent revocation.
10. Verify key export, signer replacement, recovery, and migration away from CDP.
11. Test expired and replayed links, wrong-user OAuth, compromised Discord, provider outages, lost authentication factors, and linked identities.
12. Complete security, threat-model, and jurisdiction-specific legal review before considering mainnet.

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
