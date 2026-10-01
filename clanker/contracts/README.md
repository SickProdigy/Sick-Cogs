# Pinned external contracts

This directory records reviewed external contract surfaces used by Clanker. The manifest defines the exact operation Clanker may construct; it is not wallet authorization.

`clanker-v4-base-sepolia.json` pins the official Clanker SDK v4.2.19 Base Sepolia deployment
configuration. The source tag resolves to commit `4f4d2bbf41c7f10543559dc043c85f443a6d452e`.
The factory bytecode hash was independently read from Base Sepolia at the pinned address.

The Clanker capability remains a prototype. Clanker must reject every other
network, factory, selector, ABI shape, and extension address, and it must require protected user
approval before submission. Mainnet support is intentionally absent.

`clanker-v4-base-mainnet-candidate.json` is read-only audit evidence for issue #204. It records the
current official SDK's Base deployment map and matching runtime bytecode from two independent RPC
sources. It is deliberately not imported by cog code, has `executionEnabled: false`, and does not
authorize a mainnet launch. The candidate also records the current SDK's newer locker address where
it differs from the older contracts-repository README.

Platform attribution is valid only when exactly one reward entry uses the owner-configured treasury as
both its administrator and recipient at the owner-configured immutable share, with the canonical
`SickGamingBot`/`discord` context. Saved-draft execution revalidates that invariant before calldata
is rebuilt; browser or stored-payload changes fail closed.

Authoritative sources:

- <https://github.com/clanker-devco/clanker-sdk/releases/tag/v4.2.19>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/utils/clankers.ts>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/abi/v4/Clanker.ts>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/config/clankerTokenV4.ts>
