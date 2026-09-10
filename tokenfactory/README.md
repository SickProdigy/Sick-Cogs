# TokenFactory

TokenFactory is the testnet-first fixed-supply ERC-20 deployment workflow tracked in issue #41.
It is deliberately separate from the Clanker launch cog and integrates with CryptoWallet for the
requesting user's existing Base Sepolia wallet identity.

## Current milestone

```text
[p]tokenfactory create
[p]tokenfactory status
```

`tokenfactory create` opens a requester-bound card. Clicking **Enter token details** opens a modal
for the token name, symbol, fixed supply, and decimals. The owner address is obtained from
CryptoWallet and cannot be replaced by modal input. Drafts are public metadata; no key or provider
credential is stored.

Deployment is intentionally unavailable in this milestone. Before enabling it, the repository must
contain a reviewed fixed-supply factory artifact and the installation must pin its Base Sepolia
address, ABI/version, and runtime bytecode hash. The protected approval must bind every immutable
draft parameter, and successful deployments must be verified on-chain before being submitted to
CryptoWallet's community token registry.

No mainnet network, arbitrary Solidity, arbitrary bytecode, arbitrary calldata, later minting
authority, or bot ownership is supported.
