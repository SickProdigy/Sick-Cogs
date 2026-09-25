# CryptoWallet threat model

Status: required reading for any Base mainnet review. This describes the current develop implementation and does not authorize mainnet.

## Assets and security goals

- Preserve each user wallet, provider identity, export path, and public address.
- Prevent a Discord command, companion URL, stale card, replay, or foreign deployment from authorizing a different transaction.
- Keep CDP credentials, relay secrets, JWT and TOTP private keys, TOTP seeds, exported signer keys, and recovery material outside public assets, URLs, Discord messages, and logs.
- Keep Base mainnet disabled unless every independent code, configuration, operator, value-limit, and review gate passes.
- Preserve an audit trail for transaction intents without treating Discord as blockchain authorization.

## Trust boundaries and ownership

| Component | Trusted responsibility | Must never receive or control |
| --- | --- | --- |
| Discord command/card | Collect intent and explicit confirmation from the immutable Discord user | Provider credentials, private keys, TOTP seed, unrestricted signer |
| CryptoWallet cog | Bind identity, enforce policy, encrypt TOTP state, revalidate and submit reviewed intents | Production enablement from a stored flag alone |
| Companion browser | Run published UI and short-lived enrollment/export sessions | CDP secrets, relay secret, server private keys |
| PHP/MySQL relay | Exchange one-time handoffs and bounded ciphertext | Plaintext TOTP seed/code, CDP credentials, wallet private keys |
| Coinbase CDP | Provision identities/accounts and execute authorized provider operations | Authority to bypass bot policy or Discord intent binding |
| RPC/explorer | Supply public chain state | Identity, policy, or authorization decisions |
| Bot owner | Pause, lock, recover, configure, and approve bounded future canaries | User signer private keys or TOTP seed |

The user controls exported owner keys and the consequences of sharing them. CDP controls provider infrastructure and current smart-account behavior. The bot controls restricted application credentials and local policy. Smart-account upgrade and recovery authority must be re-verified against current provider documentation before mainnet.

## Threats and current controls

### Discord compromise

An attacker may create or approve intents as the user. Controls include immutable owner binding, expiring cards, final quote refresh, active delegation checks, emergency lock, revocation, optional TOTP, exact-fingerprint TOTP binding, and one-use counters. Risk remains if Discord and the independent factor are both compromised.

### Bot or host compromise

A host attacker may access provider credentials and server-held keys. Secrets remain in Red shared API tokens or private server configuration, but host compromise is not eliminated by encryption at rest. Response requires provider pause, emergency locks, rotation, delegation revocation, reconciliation, and incident review.

### Companion or relay compromise

Controls include one-time handles, short expiry, signed deployment/application/user/purpose claims, authenticated bot-to-relay requests, RSA-OAEP enrollment, ciphertext-only relay storage, and no inbound cog listener. Browser-origin compromise can still present hostile JavaScript, so HTTPS, deployment control, integrity review, and rapid containment remain necessary.

### Replay, double click, and substitution

Intent IDs, stored pending state, owner-bound views, expiry, exact quote comparison, approval fingerprints, atomic status transitions, provider attempt IDs, durable mainnet reservations, and one-use TOTP counters prevent ordinary replay. An ambiguous provider response becomes uncertain and must be reconciled rather than resubmitted.

### Provider, RPC, and chain disagreement

Provider status or public RPC data may be stale, unavailable, or malicious. Network identity is explicit and capabilities fail closed. Mainnet remains prohibited until independent chain ID, nonce, fee, operation, balance, receipt/finality, replacement, and reorg handling are implemented and tested.

### Credential leakage

Secrets in URLs, logs, Discord, browser bundles, repository files, or broad diagnostics are compromised. Rotate the affected credential, invalidate outstanding handoffs or delegations where possible, preserve non-secret evidence, and never reproduce the secret.

### Operator error

Exact acknowledgements, owner-only commands, capability gates, emergency pause, limits, migration checksums, and separate test/production configuration reduce accidents. Production should use a second-person review for migrations, credentials, and future canaries.

## Mainnet release blockers

Base mainnet remains unavailable until issue 202 verifies provider ownership/export behavior, legal review, environment separation, final chain and nonce revalidation, monitoring and reconciliation, restart and provider-failure tests, migration/restart testing, and a separately approved owner-only low-value canary.
