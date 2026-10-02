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

1. CryptoWallet re-reads the CDP end user and accepts only the unique registered EOA owner of the user's stored smart account as the Polymarket signer. The Base smart-account address itself is never used as the signer.
2. Polymarket derives the current official Deposit Wallet address from that verified EOA automatically. Ordinary users do not connect a separate account; the prior protected existing-account flow remains compatibility-only.
3. Deposit Wallet deployment will use the official Builder/Relayer model with server-held builder credentials and exact user approval. Derivation alone never claims deployment.
4. Scoped 180-day CLOB-only session keys are the selected routine order-signing model. They remain non-withdrawing and disabled until authorization, revocation, encrypted credential storage, and recovery pass testing.
5. CDP owner signing is reserved for the narrow account-creation/authorization operations that require it; routine orders use the approved session key. The adapter accepts only Polymarket's exact chain-137 ClobAuth EIP-712 structure, requires active CryptoWallet delegation, uses the documented end-user typed-data endpoint, and remains default-off.

The pinned non-executable manifest lives in `polymarket/production_manifest.py`. It records current official endpoints, wallet types, pUSD metadata, and contract addresses and rejects drift before any future execution path can become available.

Official references: [wallets and authentication](https://docs.polymarket.com/trading/wallets-auth), [session keys](https://docs.polymarket.com/trading/session-keys), [pUSD](https://docs.polymarket.com/concepts/pusd), and [contracts](https://docs.polymarket.com/resources/contracts).
