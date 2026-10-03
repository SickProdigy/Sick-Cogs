# TokenFactory Base mainnet pre-acceptance plan

Run this only on SGBTestAgent as part of the shared CryptoWallet, TokenFactory, and Clanker checkpoint in [`../../cryptowallet/docs/operations-runbook.md`](../../cryptowallet/docs/operations-runbook.md). This plan verifies the finished default-off release candidate without authorizing a factory deployment or transaction.

## Before reload

1. Record the exact clean `develop` revision and CryptoWallet, TokenFactory, and Clanker versions.
2. Confirm the process is the authorized `agentictest` SGBTestAgent instance.
3. Back up the exact affected Red configuration files and record SHA-256 hashes without printing their contents.
4. Confirm CryptoWallet Base mainnet, TokenFactory, and Clanker are disabled and emergency-paused.
5. Confirm the pinned Base mainnet factory destination remains undeployed through the reviewed read-only RPC checks.

## Read-only Discord checks

Use the bot's configured prefix in place of `[p]`.

1. Reload CryptoWallet and TokenFactory.
2. Run `[p]tokenfactoryset mainnetstatus` and verify:
   - Base mainnet is chain `8453`.
   - The pinned factory identity, CREATE2 destination, source revision, and bytecode hashes match the committed manifests.
   - The factory is still undeployed unless a prior separately authorized acceptance recorded verified evidence.
   - TokenFactory is disabled and emergency-paused.
3. Run `[p]tokenfactory status` and one normal Base Sepolia draft review. Verify the existing testnet route remains chain `84532`, uses fixed supply and the intended recipient, displays gas and payer, and submits nothing without approval.
4. Do not run `tokenfactoryset mainnetcontrol enable`, approve a factory deployment, or confirm a token deployment during this checkpoint.

## Automated verification

1. Run the full TokenFactory suite once after the final relevant code change.
2. Run the combined CryptoWallet, TokenFactory, and Clanker compatibility coverage.
3. Reproduce the pinned contract artifacts from `tokenfactory/contracts/` and require a clean artifact diff.
4. Validate cog metadata JSON, compile the affected Python packages, and run `pip check` in the representative environment.

## Restart verification

1. Restart SGBTestAgent once using its canonical command.
2. Repeat `tokenfactoryset mainnetstatus`; the disabled and paused state must remain stable.
3. Compare configuration hashes. A second restart must not rerun a migration or change the hashes.
4. Review logs for migration loops, command collisions, secrets, provider submission, or unexpected background activity.

Stop immediately on manifest drift, a deployed factory without recorded authorization, configuration mutation, a status command that performs a provider write, or any path that exposes a mainnet approval while its capability is disabled. Proceeding to factory deployment and ordinary-member token creation requires separate explicit maintainer authorization and the live checklist in issue #203.
