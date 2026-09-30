# Independent audit handoff

This package supports the independent smart-contract/security review required by issue #203. It does
not authorize deployment, provide credentials, or enable a mainnet command.

## Review target

Review the exact public inputs below:

- Repository revision: `850a9d78a57ca6555d1f808d34fa0e6bf6a17356`
- Solidity source: `src/SickGamingTokenFactory.sol`
- Compiler: `0.8.37+commit.f401782d.Emscripten.clang`
- Optimizer: enabled, 200 runs
- Solidity metadata bytecode hash: disabled; CBOR appendix disabled
- OpenZeppelin Contracts: `5.6.1`
- Candidate manifest: `candidate-manifest.json`
- Base mainnet manifest: `manifests/base-mainnet.json`
- Predicted factory: `0xCBa30318008035BB5A855a8684cEa954D573c2C3`
- Canonical EIP-2470 singleton: `0xce0042B868300000d44A59004Da54A005ffdcf9f`
- CREATE2 salt: all-zero bytes32

Do not review files under `node_modules/` as source material. That directory is local, ignored build
state and is not committed. Resolve dependencies only from the committed lockfile with `npm ci`.

## Reproduce

From this directory in a clean checkout:

```bash
npm ci
npm test
git diff --exit-code -- artifact/SickGamingTokenFactory.json
sha256sum src/SickGamingTokenFactory.sol package-lock.json
```

`npm test` rebuilds before testing. It verifies the exposed ABI, forbidden authority selectors,
compiler settings, optimizer, metadata behavior, creation/runtime hashes, CREATE2 destination,
source and lockfile hashes, mainnet/testnet isolation, and fail-closed mainnet authorization.

Expected SHA-256 values:

```text
fde10b1846c6b9462a8d8cbf923379a7e9e9bf2466df5c1de31755a18225ac16  src/SickGamingTokenFactory.sol
c10285d16e705ee3127c08480a6479eb3e92e59a6c87d965776f74ad1591016b  package-lock.json
3177fd1f03bccdebfac94b403e7fb53ddddbe6338516de31003bfb511599c909  artifact/SickGamingTokenFactory.json
```

## Required review questions

The report must explicitly address:

1. The full supply is minted exactly once to the explicit recipient.
2. Neither the factory nor token has owner, administrator, proxy, upgrade, pause, fee, recovery, or
   later mint authority.
3. Reusing a request ID with identical parameters is idempotent; changed parameters revert.
4. CREATE2 derivation, salt use, predicted address, and singleton calldata are correct.
5. Factory registry state and `FixedSupplyTokenCreated` faithfully bind request ID, token,
   recipient, creator, and parameter hash.
6. Input bounds, zero-address/zero-supply handling, constructor behavior, reentrancy, denial of
   service, front-running, replay across chains, and gas exhaustion risks are acceptable.
7. Compiler, optimizer, metadata, dependency, ABI, creation bytecode, and runtime bytecode pins are
   reproducible.
8. The deployment and token-canary approval/lifecycle boundaries described in
   `MAINNET-READINESS.md` fail closed and do not create hidden contract authority.
9. Every finding includes severity, exploit preconditions, affected artifact, recommendation, and
   resolution status.
10. The conclusion clearly states either approved for the single owner canary under the documented
    limits or not approved.

## Deliverables

Return a signed or otherwise attributable report using `AUDIT-REPORT-TEMPLATE.md`, including the
reviewer identity, date, exact repository revision, reproduced hashes, tools/methodology, findings,
and final conclusion. Public report links may be added to the manifest only after the owner verifies
the report independently. Never include API keys, wallet secrets, private keys, TOTP seeds, relay
credentials, or signer material.
