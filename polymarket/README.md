# Polymarket

A develop-only Sick-Cogs cog for read-only prediction-market discovery and information.

## Current boundary

Version `0.2.0` provides category browsing, active-market search, trending markets, and individual market cards with market-implied probabilities, rules, resolution sources, and canonical Polymarket links. It deliberately does **not** create or custody wallets, accept deposits, sign transactions, or place orders.

The root command aliases are `polymarket` and `poly`.

## Commands

- `poly` - the complete short command guide.
- `poly markets` or `poly categories` - choose politics, crypto, or sports.
- `poly category <politics|crypto|sports>` - active markets in that category, ranked by 24-hour volume.
- `poly trending` - active markets across every category, ranked by 24-hour volume.
- `poly markets <words>` - public keyword search, excluding closed results.
- `poly market <ID, slug, or Polymarket link>` - probabilities, rules, resolution source, and canonical link.
- `poly market <word>` - attempts an exact market first, then shows useful search results when no exact slug exists.
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
