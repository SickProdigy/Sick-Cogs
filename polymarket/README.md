# Polymarket

A develop-only Sick-Cogs cog for prediction-market discovery and default-off protected Polymarket account connection.

## Current boundary

Version `0.2.15` provides category browsing, active-market search, trending markets, and individual market cards with market-implied probabilities, rules, resolution sources, and canonical Polymarket links. It also pins the current official Polygon chain, pUSD, Deposit Wallet, API endpoint, and contract-address model in a validated non-executable manifest. It deliberately does **not** create or custody wallets, accept deposits, derive credentials, sign transactions, or place orders.

Production controls are owner-only: `polyset productionstatus` shows the manifest and default-off state, while `polyset productioncontrol pause` force-closes the boundary. General production enablement deliberately refuses. The separate `polyset onboardingcontrol` can open only protected account connection and eligibility after its exact acknowledgment; every transaction capability remains disabled.

`poly account` reports the caller's connection state without accepting secrets. The develop-only account model keeps the immutable Discord user, signer address, account-wallet address, wallet type, and pending/verified/disconnected lifecycle separate. When the installation, emergency-pause, account-connect, and eligibility gates are deliberately opened for develop testing, the DM-only `poly connect` and `poly confirm` flow can connect an existing account without accepting secrets in Discord. The protected-onboarding contract requires exact signed-companion challenge binding, reviewed signer proof and account-relationship evidence, and a current browser-IP eligibility attestation before producing a verified public connection record. Its signed companion, browser proof, encrypted one-time result relay, independent Polygon derivation/deployment verification, and final public-record storage are connected; all defaults remain off and no transaction capability is enabled. Account relationship verification independently reproduces the official SDK's EOA, Proxy, Safe, legacy UUPS Deposit Wallet, and current beacon Deposit Wallet derivations, pins the SDK production RPC and factories, and requires deployed Polygon bytecode for smart wallets. Transient official ClobAuth EIP-712 signatures are independently recovered with challenge-derived nonces, strict expiry, and canonical low-S checks; only their digest is retained, and verified signer, account relationship, and eligibility evidence must agree before connection completion.

The reviewed session-key policy is Deposit-Wallet-only, beta, CLOB-scoped, fixed at 180 days, non-withdrawing, server-secret-only, owner-approved, confirmation-checked, revocable, and still non-executable. Eligibility attestations must come from the protected user's request IP, expire after five minutes, and retain country/region and blocked status without retaining the IP address.

New Deposit Wallet creation is modeled but non-executable. The design pins the current beacon-derived empty target, Polygon chain `137`, Deposit Wallet factory, relayer type `WALLET_CREATE`, metadata `Deploy Deposit Wallet`, server-only Builder/Relayer authentication, five-minute owner approval and eligibility evidence, idempotency, public transaction identifiers, restart recovery, and `STATE_CONFIRMED`/`STATE_FAILED`/`STATE_INVALID` reconciliation. No credential field or submission adapter exists.

The root command aliases are `polymarket` and `poly`.

## Commands

- `poly` - the complete short command guide.
- `poly markets` or `poly categories` - show the category chooser.
- `poly markets <politics|crypto|sports>` - shortcut directly to a category.
- `poly category <politics|crypto|sports>` - active markets in that category, ranked by 24-hour volume.
- `poly trending` - active markets across every category, ranked by 24-hour volume.
- `poly search <words>` - public keyword search, excluding closed results. Useful examples: `bitcoin`, `ethereum`, `fed rates`, and `trump`.
- `poly market <ID, slug, or Polymarket link>` - probabilities, rules, resolution source, and canonical link.
- `poly market` is intentionally singular: it only opens one exact ID, slug, or copied Polymarket link. Category words receive a category hint; failed exact references suggest `poly search`.
- `poly compatible [words]` - technically CLOB V2-ready markets for the staged future Polygon handoff; this does not check personal eligibility or enable trading.
- `poly readiness <market>` - public technical readiness details for one market.
- `poly quote <market> <outcome> <max pUSD> [max price]` - fetch the live public CLOB book and fee rate and display a two-minute, all-in bounded approval preview. It never connects an account, signs, or submits.
- `poly collateral <wrap|unwrap|standard|negative-risk> <amount> <account wallet>` - inspect exact six-decimal asset, token, spender, amount, action contract, and revocation values without approving or transacting.
- `poly account` - show the caller's secret-free account connection state.
- `poly connect <signer> <account wallet> <EOA|POLY_PROXY|GNOSIS_SAFE|DEPOSIT_WALLET>` - start default-off protected existing-account verification in DM.
- `poly confirm` - consume and independently verify the protected result in DM.
- `poly status` - the current safety boundary.

The category browser uses Polymarket's public Gamma API tags for Politics, Crypto, and Sports. General discovery remains available through explicit search and `trending`, so `markets` no longer starts with an unrelated mixed list.

## Future handoff foundation

The develop-only package includes an immutable `MarketSnapshot` parser for technically ready public CLOB markets. It records only public market identity, outcome token IDs, displayed prices, and public fee/minimum-size metadata. It is not a wallet, order, approval, quote, or transaction object.

It also includes a non-executable live order-book snapshot and market-buy approval model. The approval binds the user, market, outcome token, book hash, best ask, tick size, minimum size, negative-risk route, fee ceiling, price ceiling, all-in pUSD cap, and expiry into one fingerprint. A final refresh requires reapproval when market constraints change or price/fee ceilings are exceeded.

A restart-safe, non-executable order lifecycle model binds one approval to its Discord user, account wallet, session signer, market, token, maximum price, and maximum size. It records submission uncertainty, live and partial-fill states, cancel outcomes, provider trade evidence, and authenticated reconciliation without exposing a signing or transport path. Unknown submission and cancellation results cannot be blindly retried.

## Planned direction

1. Optional alerts and saved watchlists after the revised discovery experience is tested.
2. A separate security, legal, eligibility, and provider review for a user-controlled CryptoWallet mainnet handoff - never Discord-held funds or automatic orders.

Perpetuals, spot trading, and other market types remain separate future scopes.
