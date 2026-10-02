# Polymarket

A develop-only Sick-Cogs cog for read-only prediction-market discovery and information.

## Current boundary

Version `0.2.6` provides category browsing, active-market search, trending markets, and individual market cards with market-implied probabilities, rules, resolution sources, and canonical Polymarket links. It also pins the current official Polygon chain, pUSD, Deposit Wallet, API endpoint, and contract-address model in a validated non-executable manifest. It deliberately does **not** create or custody wallets, accept deposits, derive credentials, sign transactions, or place orders.

Production controls are owner-only: `polyset productionstatus` shows the manifest and default-off state, while `polyset productioncontrol pause` force-closes the boundary. Enablement deliberately refuses while execution has no reviewed capabilities.

`poly account` reports the caller's connection state without accepting secrets. The develop-only account model keeps the immutable Discord user, signer address, account-wallet address, wallet type, and pending/verified/disconnected lifecycle separate. Protected verification is not implemented yet, so no account can currently become connected through Discord.

The reviewed session-key policy is Deposit-Wallet-only, beta, CLOB-scoped, fixed at 180 days, non-withdrawing, server-secret-only, owner-approved, confirmation-checked, revocable, and still non-executable. Eligibility attestations must come from the protected user's request IP, expire after five minutes, and retain country/region and blocked status without retaining the IP address.

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
- `poly status` - the current safety boundary.

The category browser uses Polymarket's public Gamma API tags for Politics, Crypto, and Sports. General discovery remains available through explicit search and `trending`, so `markets` no longer starts with an unrelated mixed list.

## Future handoff foundation

The develop-only package includes an immutable `MarketSnapshot` parser for technically ready public CLOB markets. It records only public market identity, outcome token IDs, displayed prices, and public fee/minimum-size metadata. It is not a wallet, order, approval, quote, or transaction object.

## Planned direction

1. Optional alerts and saved watchlists after the revised discovery experience is tested.
2. A separate security, legal, eligibility, and provider review for a user-controlled CryptoWallet mainnet handoff - never Discord-held funds or automatic orders.

Perpetuals, spot trading, and other market types remain separate future scopes.
