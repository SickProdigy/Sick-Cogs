# TokenFactory mainnet-readiness test plan

Run this only on SGBTestAgent. This checkpoint validates fail-closed staging behavior and the
existing Base Sepolia route. It does not authorize a Base mainnet deployment or transaction.

## Before reload

1. Back up the exact TokenFactory and CryptoWallet Red configuration files and record SHA-256
   hashes.
2. Confirm the process is the authorized `agentictest` SGBTestAgent instance.
3. Confirm `develop` is clean and at or beyond commit `1ed6471`.
4. Confirm CryptoWallet mainnet capabilities remain disabled.

## Discord checks

Use the bot's configured prefix in place of `[p]`.

1. Reload CryptoWallet and TokenFactory.
2. Run `[p]tokenfactoryset mainnetstatus`.
   - Network is Base mainnet chain 8453.
   - Manifest is `candidate-not-deployed`.
   - Both singleton checks are recorded and the destination is empty.
   - Enabled is false, paused is true, and owner canary is false.
   - Independent technical review, owner risk/compliance attestation, and canary approval remain
     required.
   - Formal audit and legal review remain required before public or commercial mainnet use.
3. Run `[p]tokenfactoryset mainnetcontrol enable`.
   - The request is rejected.
   - No configuration flag changes.
   - No RPC submission, credential lookup, CDP request, or transaction occurs.
4. Run `[p]tokenfactoryset mainnetcontrol pause`.
   - The bot reports emergency pause.
   - Enabled and owner-canary flags remain false.
5. Run `[p]tokenfactory status` and one normal Base Sepolia draft review.
   - Existing testnet factory state remains unchanged.
   - The review still shows Base Sepolia 84532, fixed supply, recipient, gas, and zero native value.
   - Do not confirm a deployment unless a separate Base Sepolia regression transaction is desired.

## After reload

1. Restart SGBTestAgent once using its canonical command.
2. Repeat `mainnetstatus`; defaults and pause state must remain stable.
3. Compare configuration hashes. Only the expected new default fields may appear on the first
   migration; the second restart must be hash-stable.
4. Run the combined CryptoWallet, TokenFactory, and Clanker suites.
5. Review logs for migration loops, command collisions, provider calls, or attempted mainnet
   submission.

Stop immediately if a public command offers a mainnet confirmation button, requests mainnet
credentials, changes an authorization flag during rejected enablement, or sends an RPC transaction.
The protected canary approval component is code-only staging until the external gates are complete;
it must not be reachable from the command surface or call a provider.
