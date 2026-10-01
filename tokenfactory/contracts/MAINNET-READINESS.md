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
per day, fixed gas limits, zero native value, and no public/member deployment. Base mainnet gas is never sponsored by SickGaming: the creator wallet must pay the displayed network fee and hold at least the approved reapproval threshold before submission. No provider route
can deploy the factory or a token.

The staged owner-canary approval component binds the bot owner, wallet profile, signer, recipient,
chain, factory, request ID, calldata hash, gas limit, reapproval threshold, and payer, native value, and review fingerprint.
It requires current TokenFactory terms and the exact creator-responsibility acknowledgment, expires after ten
minutes, and can be claimed atomically only once. Emergency pause clears every pending review and
approval. The separate pre-submission validator rechecks all immutable bindings, the live chain and
factory runtime hash, signer authorization, current policy, creator-wallet balance sufficient for the approved reapproval threshold, and absence of any existing provider
operation. These components remain unreachable from a deployment command until the external gates
are complete.

The deterministic factory deployment has its own review, approval, lifecycle, and evidence
records, separate from the later token canary. Its protected card binds the canonical singleton,
predicted factory, creation code, deployment calldata, signer, gas limit, reapproval threshold, and payer, zero native value,
and an independent fingerprint. Immediately before any future submission it must recheck chain
8453, the singleton runtime pin, the still-empty predicted destination, authorization, fee policy,
and absence of a provider operation. Its approval requires the distinct
`DEPLOY BASE MAINNET FACTORY` acknowledgement plus a fresh TOTP code. Two-provider confirmation
must agree on the receipt, calldata, signer, singleton runtime, predicted address, factory runtime,
block, and the absence of ownership or upgrade authority.

A separate persistent lifecycle permits only reviewed transitions across prepared, processing,
submitted, uncertain, timed-out, confirmed, failed, dropped, and replaced states. The review
fingerprint, request ID, attempt ID, provider operation, and transaction identifiers cannot drift
during recovery. A confirmed deployment is not considered verified until two independently collected
Base mainnet RPC snapshots agree on the successful receipt, transaction and block, factory and token
runtime hashes, token metadata, fixed supply, recipient balance, creation event, and factory registry
record. Differing evidence fails closed and a different verification record cannot overwrite the
first accepted record.

An independent technical review appropriate to the finished ordinary-member release candidate, an explicit owner
free-release scope attestation, explicit maintainer approval for controlled develop testing, source verification, evidence
recording, and combined test-bot validation remain gates. The attestation confirms that SickGaming
does not sell, promote, endorse, list, trade, provide liquidity for, or promise returns on created
tokens and charges no TokenFactory fee. Issue #229 tracks the concise acknowledgments required before
free public/member use. A formal paid audit or legal opinion is not a launch gate for that scoped
free creation tool; issue #228 requires outside counsel before monetization, custody, trading,
promotion, or other expanded financial services.
