# TokenFactory

TokenFactory is the testnet-first fixed-supply ERC-20 deployment workflow tracked in issue #41.
It is deliberately separate from the Clanker launch cog and integrates with CryptoWallet for the
requesting user's existing Base Sepolia wallet identity.

## Protected Base Sepolia deployment

```text
[p]tokenfactory create
[p]tokenfactory deployment
[p]tokenfactory status
[p]tokenfactoryset deployment enable
[p]tokenfactoryset deployment pause
[p]tokenfactoryset deployment disable
[p]tokenfactoryset deployfactory
[p]tokenfactoryset verifyfactory
```

`tokenfactory create` opens a requester-bound card for the token name, symbol, fixed supply, and
decimals. The recipient is always the member's current Base Sepolia CryptoWallet smart account and
cannot be replaced by input. A second requester-bound confirmation card displays every immutable
field before submitting anything.

Member deployment remains disabled and emergency-paused until the bot owner runs
`tokenfactoryset deployment enable`. Enablement re-verifies the exact pinned factory on Base
Sepolia. `pause` immediately blocks new submissions without discarding configuration; `disable`
blocks submissions and disables the feature. Existing on-chain operations are never reversed by a
pause.

A deployment requires an active CryptoWallet authorization. CryptoWallet encodes only the reviewed
`createFixedSupplyToken` method, fixes the destination to the pinned factory, requires the recipient
to match the requesting wallet profile, and submits it through sponsored Base Sepolia smart-account
execution. Each draft receives a retry-safe request ID.

After confirmation, `tokenfactory deployment` refreshes the operation and verifies the factory's
request mapping, token name, symbol, decimals, recipient balance, and total supply. Only a matching
token is recorded and added to CryptoWallet's shared registry as a community token.

No mainnet network, arbitrary Solidity, arbitrary bytecode, arbitrary calldata, later minting
authority, upgrade path, administrator, or bot ownership is supported.

The pinned contract source and reproducible build live under [`contracts/`](contracts/).
