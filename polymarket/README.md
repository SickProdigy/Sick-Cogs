# Polymarket

An early, develop-only Sick-Cogs cog for read-only prediction-market discovery and information.

## Current boundary

Version `0.1.11` provides active-market search plus individual market cards with market-implied probabilities, rules, resolution source, and canonical Polymarket links. It deliberately does **not** connect a provider, create or custody wallets, accept deposits, sign transactions, or place orders.

## Commands

- `polymarket` — a compact read-only command overview.
- `polymarket markets` or `polymarket trending` — active markets ranked by 24-hour volume.
- `polymarket markets <words>` — public keyword search, excluding closed results.
- `polymarket market <ID, slug, or Polymarket link>` — probabilities, rules, resolution source, and canonical link.
- `polymarket compatible [words]` — technically CLOB V2 order-ready markets for the staged future Polygon handoff; it does not check personal eligibility or enable trading.
- `polymarket status` — confirms the safety boundary.

## Future handoff foundation

The develop-only package includes an immutable `MarketSnapshot` parser for technically ready public CLOB markets. It records only public market identity, outcome token IDs, displayed prices, and public fee/minimum-size metadata. It is not a wallet, order, approval, quote, or transaction object.

## Planned direction

1. Optional alerts and saved watchlists.
2. A separate security, legal, and provider review for a user-controlled CryptoWallet mainnet handoff—never Discord-held funds or automatic orders.

Perpetuals, spot trading, and other market types remain separate future scopes.
