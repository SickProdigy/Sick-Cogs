# Base mainnet readiness

This freezes the candidate contract behavior and release boundary for issue #203. It does not
authorize deployment.

## Frozen behavior

The source creates one non-upgradeable fixed-supply ERC-20 per unique request ID and parameter set.
The complete supply is minted once in the constructor to the explicit recipient. Neither contract
has an owner, administrator, proxy, upgrade, pause, recovery, fee, burn-from, or later mint path.

The factory stores only the token address and immutable parameter hash. An identical retry returns
the original token; reusing an ID with different parameters reverts. The event records the request
ID, token, recipient, caller, and parameter hash.

## Reproducibility and isolation

Compiler and dependency versions, optimizer settings, source and lock hashes, bytecode hashes, ABI,
selector, event topic, singleton, zero salt, and deterministic destination are pinned. Appended CBOR
metadata is disabled so identical pinned inputs produce identical bytecode.

Mainnet and Sepolia use separate manifests even though the same EIP-2470 bytecode and salt derive
the same address. Chain IDs, observations, authorization, lifecycle records, credentials, and
evidence must never fall back between environments.

## Current boundary

On 2026-09-30, Base's official RPC and PublicNode independently reported chain 8453, matching
EIP-2470 singleton code, and no code at the predicted factory destination. These observations verify
the pre-deployment destination only; deployed source, runtime code, configuration, and events still
require two-provider verification after an explicitly approved canary.

Every mainnet authorization flag is false. The owner-only `mainnetcontrol` command can pause or
disable immediately; its enable request fails with no state change while external gates remain.
Conservative immutable canary ceilings permit at most one factory deployment, one token deployment
per day, fixed gas ceilings, zero native value, and no public/member deployment. No provider route
can deploy the factory or a token.

The staged owner-canary approval component binds the bot owner, wallet profile, signer, recipient,
chain, factory, request ID, calldata hash, gas ceiling/payer, native value, and review fingerprint.
It requires the exact permanent-loss acknowledgement plus a fresh TOTP code, expires after ten
minutes, and can be claimed atomically only once. Emergency pause clears every pending review and
approval. The separate pre-submission validator rechecks all immutable bindings, the live chain and
factory runtime hash, signer authorization, current policy, and absence of any existing provider
operation. These components remain unreachable from a deployment command until the external gates
are complete.

A separate persistent lifecycle permits only reviewed transitions across prepared, processing,
submitted, uncertain, timed-out, confirmed, failed, dropped, and replaced states. The review
fingerprint, request ID, attempt ID, provider operation, and transaction identifiers cannot drift
during recovery. A confirmed canary is not considered verified until two independently collected
Base mainnet RPC snapshots agree on the successful receipt, transaction and block, factory and token
runtime hashes, token metadata, fixed supply, recipient balance, creation event, and factory registry
record. Differing evidence fails closed and a different verification record cannot overwrite the
first accepted record.

Independent security review, legal review, explicit owner canary approval, source verification,
evidence recording, and combined test-bot validation remain gates.
