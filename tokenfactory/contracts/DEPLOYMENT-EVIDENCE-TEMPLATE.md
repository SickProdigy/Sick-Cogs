# Base mainnet deployment evidence record

Complete this only after all audit, legal, and explicit owner gates are satisfied. This template does
not authorize a transaction.

## Authorization

- Issue #203 audit report:
- Audit report hash/signature verified by:
- Legal review reference:
- Explicit owner authorization reference and timestamp:
- Approved factory review fingerprint:
- TOTP-protected approval timestamp/expiry:
- Factory attempt ID:
- Token-canary review fingerprint:
- Token-canary attempt ID:

## Pre-submission evidence

Record both providers independently.

| Check | Provider 1 | Provider 2 |
|---|---|---|
| Provider and endpoint | | |
| Observation time UTC | | |
| Chain ID = 8453 | | |
| Latest block number/hash | | |
| Singleton address | | |
| Singleton runtime SHA-256 | | |
| Predicted factory address | | |
| Predicted destination empty | | |
| Signer/profile binding verified | | |
| Authorization active | | |
| Fee ceiling and payer | | |
| Native value = 0 | | |
| Provider operation state = not-created | | |

## Factory deployment

- Transaction hash:
- User-operation/provider identifier:
- Submission timestamp UTC:
- Confirmed block number/hash:
- Singleton target:
- Calldata SHA-256:
- Gas limit:
- Maximum gas fee:
- Actual fee:
- Native value:
- Predicted/deployed factory:
- Factory runtime code hash:
- Source-verification URL:
- Owner/admin authority absent:
- Upgrade/proxy authority absent:

## Token canary

- Request ID:
- Transaction hash:
- User-operation/provider identifier:
- Confirmed block number/hash:
- Factory target:
- Calldata SHA-256:
- Token address:
- Name/symbol/decimals:
- Total fixed supply:
- Recipient:
- Recipient balance:
- Creation event topic and decoded fields:
- Factory registry token and parameter hash:
- Token runtime code hash:
- Source-verification URL:
- Native value:
- Maximum and actual gas fee:

## Independent post-transaction verification

| Check | Provider 1 | Provider 2 |
|---|---|---|
| Receipt success | | |
| Transaction/block match | | |
| Factory runtime match | | |
| Token runtime match | | |
| Metadata match | | |
| Total supply match | | |
| Recipient balance match | | |
| Event match | | |
| Registry match | | |

- Stored factory evidence fingerprint/hash:
- Stored token evidence fingerprint/hash:
- Explorer links:
- Any disagreement or anomaly:
- Final reviewer:
- Final verification time UTC:

Never record credentials, private keys, TOTP codes/seeds, relay secrets, raw authorization tokens, or
unrestricted signer material.
