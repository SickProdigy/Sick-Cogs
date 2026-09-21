# Pinned external contracts

This directory records reviewed external contract surfaces used by CryptoWallet. A manifest is an
allowlist input, not authorization to sign or submit a transaction.

`clanker-v4-base-sepolia.json` pins the official Clanker SDK v4.2.19 Base Sepolia deployment
configuration. The source tag resolves to commit `4f4d2bbf41c7f10543559dc043c85f443a6d452e`.
The factory bytecode hash was independently read from Base Sepolia at the pinned address.

The Clanker capability remains a prototype. Code consuming this manifest must reject every other
network, factory, selector, ABI shape, and extension address, and it must require protected user
approval before submission. Mainnet support is intentionally absent.

Authoritative sources:

- <https://github.com/clanker-devco/clanker-sdk/releases/tag/v4.2.19>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/utils/clankers.ts>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/abi/v4/Clanker.ts>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/config/clankerTokenV4.ts>
