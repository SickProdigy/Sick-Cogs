# CryptoWallet operations and incident runbook

This covers companion relay and bot controls. It does not authorize mainnet or production access by an agent.

## Planned deployment

1. Back up exact CryptoWallet and Red configuration files plus the companion database; record hashes without printing secrets.
2. Deploy the complete reviewed cryptowallet web tree.
3. Run: php /absolute/path/to/cryptowallet/web/server/migrate.php
4. Verify sickwallet_schema_migrations contains expected immutable filenames and checksums.
5. Update and reload the reviewed cog version in the test instance first.
6. Verify relay readiness, enrollment, invalid and replayed TOTP rejection, normal non-TOTP sends, emergency lock, revocation, disable, replacement, and locked recovery.
7. Restart the test bot and verify configuration hashes, loaded cogs, connection, logs, pending-intent recovery, and migration idempotency.
8. Stage through main only after the issue checklist and release review are complete.

Never rerun the public setup wizard for an existing installation. Never edit a released migration; add the next numbered migration.

## Routine monitoring

Review walletset view, walletset usage, pending and uncertain intents, provider billing, relay errors, database growth, migration state, and current provider behavior. Alerts must not include tokens, JWTs, TOTP material, authorization headers, private keys, compact configuration files, or secret-bearing request bodies.

## Immediate containment

### Suspected Discord-user compromise

1. Run walletset lock with the immutable user ID.
2. Confirm pending intents were rejected and the local lock is active.
3. Retry provider delegation revocation if the initial attempt failed.
4. Preserve intent IDs and public transaction identifiers.
5. Do not unlock until independent identity review and reconciliation finish.

### Lost authenticator

1. User runs wallet security lock and contacts the bot owner.
2. Owner independently verifies identity.
3. While locked, run walletset 2fareset with the user ID and exact acknowledgement shown by the command.
4. Review and revoke provider authorization separately.
5. Unlock only after review; require fresh protected authorization and TOTP enrollment as appropriate.

### Provider or chain outage

1. Run walletset pause.
2. Do not resubmit uncertain operations.
3. Record intent IDs, attempt IDs, public hashes, provider status, and timestamps.
4. Reconcile provider and chain state before resume.
5. Resume only after duplicate-submission risk is understood.

### Bot, companion, relay, or credential compromise

1. Pause provider processing and keep mainnet disabled.
2. Isolate the service without deleting evidence.
3. Rotate affected credentials through private stores.
4. Invalidate outstanding sessions and revoke delegations where supported.
5. Audit unauthorized intents and public transactions.
6. Restore reviewed code and verified backups, apply migrations, and test in isolation.
7. Document scope, decisions, user impact, and follow-up controls.

## Uncertain transaction rule

An operation that may have reached the provider is not failed merely because the bot timed out. Keep it uncertain, reconcile by original attempt and public identifiers, and never create a replacement until non-submission is established.

## Mainnet canary boundary

Any future pre-2.0 canary requires separate explicit approval, bot-owner-only access, tiny disposable funds, complete disclosures, configured limits, monitoring, and acceptance that permanent loss is possible. Public mainnet remains prohibited.
