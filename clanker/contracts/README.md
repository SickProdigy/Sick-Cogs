# Pinned external contracts

This directory records reviewed external contract surfaces used by Clanker. The manifest defines the exact operation Clanker may construct; it is not wallet authorization.

`clanker-v4-base-sepolia.json` pins the official Clanker SDK v4.2.19 Base Sepolia deployment
configuration. The source tag resolves to commit `4f4d2bbf41c7f10543559dc043c85f443a6d452e`.
The factory bytecode hash was independently read from Base Sepolia at the pinned address.

The Clanker capability remains a prototype. Clanker rejects every unreviewed network, factory,
selector, ABI shape, and extension address. Mainnet staging is read-only and must require protected
user approval before any future submission; the real submitter remains intentionally absent.

`clanker-v4-base-mainnet-candidate.json` is read-only audit evidence for issue #204. It records the
current official SDK's Base deployment map and matching runtime bytecode from two independent RPC
sources. It is loaded only by the fail-closed validation and evidence modules, has
`executionEnabled: false`, and does not authorize a mainnet launch. The candidate also records the current SDK's newer locker address where
it differs from the older contracts-repository README.

Platform attribution is valid only when exactly one reward entry uses the owner-configured treasury as
both its administrator and recipient at the owner-configured immutable share, with the canonical
`SickGamingBot`/`discord` context. Saved-draft execution revalidates that invariant before calldata
is rebuilt; browser or stored-payload changes fail closed.

The candidate operation allowlist records exact function signatures, four-byte selectors, state
mutability, target contracts, and required success-event topics. Creator buy-in is permitted only as
part of the immutable launch extension; reward, vault, and airdrop administrative mutation functions
and owner withdrawals are explicitly excluded from user operations.

`mainnet_operations.py` turns those audited entries into immutable, expiring operation intents. It
binds the expected chain, signer, target, value, complete calldata, gas limit, fee ceiling, recipients,
and requester into a deterministic fingerprint, then compares every provider candidate exactly. The
module contains no submitter and `MAINNET_SUBMISSION_ENABLED` remains `False`.
Launch, reward, treasury, vault, and airdrop calldata is independently reconstructed from typed token,
fee-owner, recipient, amount, and Merkle-proof fields; opaque calldata cannot disagree with the
review data.

Authoritative sources:

- <https://github.com/clanker-devco/clanker-sdk/releases/tag/v4.2.19>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/utils/clankers.ts>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/abi/v4/Clanker.ts>
- <https://github.com/clanker-devco/clanker-sdk/blob/v4.2.19/src/config/clankerTokenV4.ts>

`mainnet_lifecycle.py` persists immutable attempt bindings across restarts, rejects changed provider
identifiers and invalid transitions, and requires matching receipt/state evidence from two independent
RPC observations. Runtime code, transaction fields, success events, and launch administrator are
checked against the reviewed intent and manifest before evidence is accepted.

Protected mainnet approval is requester-bound, terms-gated, fingerprint-bound, limited to ten minutes
or the shorter intent lifetime, persisted in the existing Red namespace, and atomically consumable once.
The approval button records consent only; it cannot submit while the manifest and submitter remain disabled.

Immediately before any future provider call, live revalidation repeats the exact candidate check and
requires the pinned target runtime, active wallet authorization, an unchanged gas quote, enough signer
balance for native value plus maximum fee, and a clean not-created provider state. ERC-4337 nonce is
provider-managed; duplicate prevention uses the immutable operation ID and clean provider state.
