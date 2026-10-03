# CryptoWallet operations and incident runbook

This covers companion relay and bot controls. It does not authorize mainnet or production access by an agent.

## Planned deployment

1. Back up exact CryptoWallet and Red configuration files plus the companion database; record hashes without printing secrets.
2. Deploy the complete reviewed cryptowallet web tree.
3. Visit `/cryptowallet/setup/` over HTTPS and select **Run database update**.
4. Verify sickwallet_schema_migrations contains expected immutable filenames and checksums.
5. Update and reload the reviewed cog version in the test instance first.
6. Verify relay readiness, enrollment, invalid and replayed TOTP rejection, normal non-TOTP sends, emergency lock, revocation, disable, replacement, and locked recovery.
7. Restart the test bot and verify configuration hashes, loaded cogs, connection, logs, pending-intent recovery, and migration idempotency.
8. Stage through main only after the issue checklist and release review are complete.

## Base stack pre-acceptance checkpoint

This checkpoint covers CryptoWallet, TokenFactory, and Clanker together. It is read-only and nontransactional. Stop before any `enable` command, capability change, factory deployment, token creation, Clanker launch, or real-value send unless the maintainer separately authorizes controlled acceptance.

1. Record the exact clean `develop` commit and the versions from `cryptowallet/info.json`, `tokenfactory/info.json`, and `clanker/info.json`.
2. Verify the target process is the authorized `agentictest` SGBTestAgent and that no process owned by `terry` will be touched.
3. Back up the exact affected cog and core configuration files plus the companion database and private `recovery-config.local.php`. Record SHA-256 hashes without printing file contents or secret values.
4. Run the CryptoWallet, TokenFactory, Clanker, and companion browser suites in the representative environment. Validate cog JSON, compile changed Python, run `pip check`, and reproduce the pinned TokenFactory contract artifacts.
5. Run `[p]walletset cdpstatus`, `[p]walletset cdpcheck`, `[p]walletset jwtstatus`, `[p]walletset mainnet preflight`, and `[p]walletset mainnet status`. The checks must remain read-only; Base mainnet stays disabled and paused with every capability off.
6. Run `[p]tokenfactoryset mainnetstatus`. The pinned factory remains undeployed until separately authorized; TokenFactory remains disabled and paused.
7. Run `[p]clankerset mainnetstatus`. Clanker remains disabled and paused, with its reviewed contract manifest intact.
8. Restart SGBTestAgent with its canonical command, then confirm the process owner, Discord connection, complete baseline cog set, configuration hashes, migration idempotency, and absence of attempted mainnet submission in logs.
9. Record only non-secret evidence: revision, versions, test totals, configuration hashes, public manifest or bytecode hashes, command status, and timestamps.

A failed preflight, changed identity, manifest drift, unexpected configuration mutation, missing backup, dependency conflict, or provider call from a status-only command blocks acceptance. Do not compensate by opening a gate or submitting a probe transaction.

The setup page detects an existing private configuration and exposes only the database-update action; it does not display connection or migration details. Never edit a released migration; add the next numbered migration.

## Routine monitoring

Review walletset view, walletset usage, pending and uncertain intents, provider billing, relay errors, database growth, migration state, and current provider behavior. Alerts must not include tokens, JWTs, TOTP material, authorization headers, private keys, compact configuration files, or secret-bearing request bodies. CryptoWallet DMs configured bot owners on the first monthly CDP rate-limit response, the first provider server-error class, a restart-interrupted submission, and a transaction that becomes uncertain after 24 hours. Provider alerts are severity-deduplicated for the accounting period; transaction alerts are emitted once at the state transition. Treat an alert as a prompt to inspect `walletset usage`, pause when errors persist, preserve public identifiers, and reconcile before any replacement.

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

## Mainnet acceptance boundary

The first develop smoke tests require explicit maintainer approval, tiny disposable funds, complete disclosures, configured limits, monitoring, and acceptance that permanent loss is possible. They exercise the same ordinary-member flow intended for release; they are not an owner-only product mode. Public release remains prohibited until the finished candidate passes acceptance and outside review.
