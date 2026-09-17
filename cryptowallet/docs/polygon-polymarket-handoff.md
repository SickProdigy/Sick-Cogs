# Polygon / Polymarket handoff boundary

This is a develop-only design boundary. It does not enable Polygon in CryptoWallet.

## Known facts

- Polymarket CLOB V2 uses Polygon mainnet, chain ID `137`.
- Its production collateral is pUSD.
- A market is only technically suitable when public data reports an active, accepting CLOB order book with outcome-token IDs.
- CryptoWallet's existing Base Sepolia account must not be assumed to be a deployed or controllable Polygon account.

## Permitted before mainnet review

- Public RPC reads from a reviewed HTTPS endpoint: chain ID, native balance, token metadata/balance, transaction lookup, and explorer links.
- Public Polymarket market discovery, immutable snapshots, bounded future-intent data, and readiness cards.
- One-time handoff state that stores only an opaque-handle digest and public bindings.

## Prohibited until explicit release approval

- Polygon account provisioning, balance display in normal wallet cards, deposits, collateral wrapping, allowance approval, transaction construction, signing, relaying, order posting, cancellation, or automated trading.
- Reusing a Base account/address as a Polygon account without separately proving ownership, deployment, recovery, and control semantics.
- Sending secrets, provider credentials, private keys, recovery phrases, or browser authorization material to an RPC or Polymarket endpoint.

## Candidate execution models to evaluate

1. A distinct CDP Polygon smart account with user-controlled approval and recovery.
2. A user-owned external Polygon wallet connected only through a protected, explicit approval handoff.
3. A self-hosted/provider-neutral smart-account stack only if its recovery, gas, replay, and operational burden justify it.

The first candidate is not selected merely because CDP documents Polygon support; its account identity, pUSD flow, CLOB V2 signing, per-user eligibility, fees, revocation, and failure recovery must be proven first.
