# Polymarket

Polymarket is a develop-only connector that lets users browse and trade Polymarket prediction markets through Discord. CryptoWallet remains the user's wallet and funding home; this cog manages the separate Polymarket Deposit Wallet, market positions, and protected trade workflows.

Current version: `0.2.50`

## Status

The complete intended flow is implemented and tested with simulated signatures and provider responses. Public market discovery is usable, but every production capability is disabled by default, the emergency pause starts active, and monetary limits start at zero. This release cannot enable general production execution.

No live wallet signature, Deposit Wallet deployment, deposit, order, claim, withdrawal, or other mainnet transaction was performed during validation.

## How it works

1. `poly account` reads the user's existing CryptoWallet profile and verifies its CDP-managed EOA owner. Users do not enter a seed phrase, private key, or separate wallet credential.
2. The cog derives that owner's official Polygon Deposit Wallet. If it has not been deployed, the protected session flow can prepare its owner-approved deployment when the relevant gates are deliberately enabled.
3. `poly session` creates a Polymarket-only session signer for routine CLOB orders. The signer and its CLOB credentials are encrypted at rest and bound to the Discord user, CryptoWallet profile, owner, Deposit Wallet, and installation.
4. `poly deposit` uses CryptoWallet's normal protected approval flow to send a supported Base asset through Polymarket's Bridge and receive Polygon pUSD in the Deposit Wallet.
5. Buy, sell, cancel, and claim commands use immutable Discord approval cards. The first approval is always required; users may keep or disable the default-on second Yes/No confirmation.
6. `poly withdraw` obtains one narrowly constrained CryptoWallet owner signature, unwraps pUSD to USDC.e, and transfers it to a Bridge address bound to the user's existing CryptoWallet destination.

The session signer is CLOB-scoped, expires after 180 days, supports protected rotation and revocation, and cannot authorize withdrawals. Claims and withdrawals require an exact-purpose signature from the CryptoWallet owner because they move assets through the Deposit Wallet outside routine order signing.

## Funds and identities

The integration keeps these identities separate:

- **CryptoWallet account:** the user's funding and withdrawal destination.
- **Owner signer:** the CDP-managed EOA verified from the CryptoWallet profile.
- **Deposit Wallet:** the official Polymarket wallet derived from that owner on Polygon chain `137`.
- **Session signer:** an encrypted, revocable signer authorized only for routine Polymarket CLOB activity.

The cog stores public wallet identifiers, lifecycle records, immutable approval details, transaction evidence, and encrypted session material. It never asks users to paste wallet secrets into Discord. Public balance, position, and market responses are read live and are not persisted.

## Approval and recovery rules

- Transaction commands are DM-only and require current protected eligibility.
- Every action is bound to the requesting user, wallet identities, exact amount or order, expiry, and current provider data.
- The optional second confirmation defaults on. Turning it off never removes the first approval card.
- Final approval refreshes material market, balance, nonce, and identity constraints. Drift fails closed or requires a new approval where appropriate.
- Submission state is persisted before authenticated network I/O.
- An interrupted or ambiguous submission is never blindly retried. Status commands reconcile the exact stored identity and transaction evidence.
- Session private keys and CLOB credentials use identity-bound AES-256-GCM encryption with a server-side wrapping key.
- Terms, connection, and audit records retain bounded metadata and SHA-256 digests rather than signatures or secrets.

## User commands

The root commands are `polymarket` and `poly`.

### Discover markets

- `poly markets` or `poly categories` - open the category browser.
- `poly markets <politics|crypto|sports>` - open a category directly.
- `poly category <politics|crypto|sports>` - list active markets ranked by 24-hour volume.
- `poly trending` - list active markets across all categories by 24-hour volume.
- `poly search <words>` - search active market questions.
- `poly market <ID, slug, or Polymarket link>` - show probabilities, rules, resolution source, and the canonical link.
- `poly compatible [words]` - list markets that are technically compatible with the supported CLOB path.
- `poly readiness <market>` - show technical readiness for one market.
- `poly quote <market> <outcome> <max pUSD> [max price]` - show a live, two-minute bounded buy preview without signing or submitting.

### Account and safety

- `poly status` - show the current safety boundary.
- `poly account` - derive and display the CryptoWallet-owned signer and Deposit Wallet.
- `poly terms` and `poly termsconfirm` - review and accept the current product terms through the protected companion flow.
- `poly session` - create, resume, rotate, or reconcile the protected session authorization.
- `poly confirmations [on|off]` - show or change the optional second confirmation.
- `poly audit` - show the caller's latest ten digest-only safety events.
- `poly disconnect` - clear pending connection data and the public account binding, including while paused.

`poly connect` and `poly confirm` provide compatibility for an existing Polymarket account. They are not part of the normal bot-first CryptoWallet flow, and users must never send wallet secrets in Discord.

### Funds and portfolio

- `poly deposit <ETH amount>` - prepare a CryptoWallet Bridge deposit card or resume its current status.
- `poly withdraw <pUSD amount> [Base asset]` - prepare a protected withdrawal to the existing CryptoWallet address.
- `poly withdrawstatus` - reconcile the Polygon withdrawal and exact Bridge arrival without resubmitting.
- `poly balance` - show available pUSD and open-position value for the bound Deposit Wallet.
- `poly positions` - show up to ten current identity-bound positions.
- `poly orders` - show up to ten authenticated open orders for the active session.
- `poly collateral <wrap|unwrap|standard|negative-risk> <amount> <account wallet>` - inspect an exact collateral route without approving or transacting.

### Trade and settle

- `poly buy <market> <outcome> <max pUSD> [max price]` (alias `poly bet`) - prepare an immutable fill-and-kill buy with an all-in spending cap.
- `poly sell <market> <outcome> <shares> <min price>` - prepare an immutable fill-and-kill sell with a price floor.
- `poly orderstatus` (alias `poly order`) - reconcile the exact stored order without retrying submission.
- `poly cancel [order ID]` - prepare cancellation of the exact live or partially filled order.
- `poly claim <market> <outcome>` (alias `poly redeem`) - prepare redemption of a resolved winning position.
- `poly claimstatus` (alias `poly redeemstatus`) - reconcile the exact claim transaction and cleared position.

## Owner controls

- `polyset productionstatus` - show the pinned manifest, emergency pause, capabilities, session policy, and limits.
- `polyset productioncontrol pause|disable` - close the production boundary. `enable` deliberately refuses in this release.
- `polyset onboardingcontrol enable|pause|disable` - control only the protected, non-transactional compatibility onboarding path; enabling requires the exact acknowledgment shown by the command.
- `polyset limits <per order> <per user/day> <installation/day>` - store ordered six-decimal pUSD caps without enabling any capability.

Capabilities are separately gated for account connection, eligibility, Deposit Wallet creation, sessions, deposits, account reads, orders, cancellations, claims, and withdrawals. Changing limits does not enable any of them.

## Pinned production model

The validated manifest pins Polygon chain `137`, six-decimal pUSD collateral, the current Exchange, negative-risk Exchange, Conditional Tokens, collateral adapters, Deposit Wallet factory and implementation model, Gamma API, Data API, CLOB API, Builder/Relayer API, and Bridge API.

The order path supports the current protocol-v2 and protocol-v3 asset namespaces, signature type `3`, SDK-equivalent tick rounding and fee calculation, authenticated CLOB transport, and deterministic order-hash recovery after ambiguous submission. Settlement supports the reviewed legacy CTF and protocol-v3 redemption routes. Withdrawal binds the exact pUSD approval, pUSD-to-USDC.e unwrap, and USDC.e Bridge transfer call order.

## Validation

The current implementation passed:

- Polymarket tests: `168/168`
- CryptoWallet tests: `263/263`
- Companion browser security tests: `11/11`
- Ordinary-member integration matrix: `20/20`

The remaining release gates are independent verification of live CDP Polygon EIP-712 behavior, a separately authorized minimal-value mainnet exercise, security review, and any required policy or legal review. Perpetuals, spot trading, and other market types remain outside this cog's scope.
