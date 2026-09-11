# Candidate fixed-supply factory

This directory contains the candidate Base Sepolia contract for issue #41. It is not audited,
deployed, or enabled by the cog merely because it compiles.

The factory accepts only token metadata, exact atomic supply, recipient, and a 32-byte request ID.
It deterministically deploys one fixed ERC-20 template, mints the complete supply to the recipient,
and retains no ownership, minting, pause, upgrade, or recovery authority. Repeating the same request
ID and parameters returns the original token; reusing an ID with different parameters reverts.

Reproducible build:

```bash
npm ci
npm run build
npm test
```

The compiler, OpenZeppelin Contracts, and hashing library are pinned exactly in `package-lock.json`.
The build disables the Solidity metadata bytecode hash and writes generated artifacts under the
ignored `build/` directory. Before deployment, independently review the Solidity source and record
the generated factory runtime code hash, factory address, compiler settings, and transaction.

The candidate manifest pins the canonical EIP-2470 singleton factory, its verified Base Sepolia
runtime code hash, and the deterministic zero-salt destination. Before submission, the deployment
path must recheck the singleton code hash and confirm that the destination has no code. It may then
send only the pinned creation bytecode and must verify the deployed runtime hash afterward.
