# Polygon / Polymarket handoff boundary

This is a develop-only design boundary. It does not enable Polygon in CryptoWallet.

## Known facts

- Polymarket CLOB V2 uses Polygon mainnet, chain ID `137`.
- Its production collateral is pUSD.
- A market is only technically suitable when public data reports an active, accepting CLOB order book with outcome-token IDs.
- New Polymarket accounts use a Deposit Wallet; legacy accounts may use Proxy or Safe wallets. The signer address and Polymarket account wallet are separate identities and must be stored and verified separately.
- CryptoWallet's existing Base account must not be assumed to be the Polygon signer or Polymarket account wallet.
- pUSD is a 6-decimal Polygon ERC-20 wrapper. Wrapping USDC.e requires a separate allowance to the official CollateralOnramp.

## Permitted while execution remains disabled

- Public RPC reads from a reviewed HTTPS endpoint: chain ID, native balance, token metadata/balance, transaction lookup, and explorer links.
- Public Polymarket market discovery, immutable snapshots, bounded future-intent data, and readiness cards.
- A typed user-scoped Polygon context containing only the Discord user ID, opaque wallet profile ID, optional public address, and an explicitly empty reviewed-capability set.
- One-time handoff state that stores only an opaque-handle digest and public bindings, including chain 137, purpose, intent fingerprint, expiry, and replay state. The shared companion signer and encrypted one-time relay recognize a dedicated five-minute `polymarket_connect` purpose and reject fields outside the exact public onboarding schema; browser proof and result transport remain disabled.

## Prohibited until the protected execution flow is implemented, tested, and explicitly enabled

- Polygon account provisioning, balance display in normal wallet cards, deposits, collateral wrapping, allowance approval, transaction construction, signing, relaying, order posting, cancellation, or automated trading.
- Reusing a Base account/address as a Polygon account without separately proving ownership, deployment, recovery, and control semantics.
- Sending secrets, provider credentials, private keys, recovery phrases, or browser authorization material to an RPC or Polymarket endpoint.

## Selected integration direction

1. Existing Polymarket users connect their explicit account wallet plus signer through a protected handoff; neither identity is inferred from a Base address.
2. New users may later create a Polymarket Deposit Wallet only through the official Builder/Relayer model with server-held builder credentials and user-controlled signing.
3. Scoped, time-limited session keys are the preferred future delegated-trading model, but remain disabled until their exact permissions, expiry, revocation, storage, and recovery behavior pass review.
4. Direct CDP Polygon signing remains an adapter candidate only if CDP can produce every required EIP-712 signature without exposing keys and the resulting signer is proven to control the separately recorded Polymarket account wallet.

The pinned non-executable manifest lives in `polymarket/production_manifest.py`. It records current official endpoints, wallet types, pUSD metadata, and contract addresses and rejects drift before any future execution path can become available.

Official references: [wallets and authentication](https://docs.polymarket.com/trading/wallets-auth), [session keys](https://docs.polymarket.com/trading/session-keys), [pUSD](https://docs.polymarket.com/concepts/pusd), and [contracts](https://docs.polymarket.com/resources/contracts).
