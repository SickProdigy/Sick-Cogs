# Polymarket

An early, develop-only Sick-Cogs cog for read-only prediction-market discovery and information.

## Current boundary

Version `0.1.8` provides active-market search plus individual market cards with market-implied probabilities, rules, resolution source, and canonical Polymarket links. It deliberately does **not** connect a provider, create or custody wallets, accept deposits, sign transactions, or place orders.

## Commands

- `polymarket` — a compact read-only command overview.
- `polymarket markets` or `polymarket trending` — active markets ranked by 24-hour volume.
- `polymarket markets <words>` — public keyword search, excluding closed results.
- `polymarket market <ID, slug, or Polymarket link>` — probabilities, rules, resolution source, and canonical link.
- `polymarket status` — confirms the safety boundary.

## Planned direction

1. Optional alerts and saved watchlists.
2. A separate security, legal, and provider review for a user-controlled CryptoWallet mainnet handoff—never Discord-held funds or automatic orders.

Perpetuals, spot trading, and other market types remain separate future scopes.
