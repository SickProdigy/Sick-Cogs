# TokenFactory

TokenFactory is the testnet-first fixed-supply ERC-20 deployment workflow introduced in issue #41
and extended with provider-independent wallet deployment in issue #112. It is deliberately separate
from the Clanker launch cog.

## Ownership boundary

TokenFactory owns its drafts, validation, canonical factory calldata, execution terms, pending
deployment lifecycle, external-transaction verification, on-chain token verification, and result
records. CryptoWallet is a narrow signer and transport dependency: it binds the Discord wallet,
enforces authorization, pause and security-lock state, independently checks the pinned call
allowlist, submits the reviewed operation through CDP, and hosts the shared static companion and
opaque relay. CryptoWallet does not construct TokenFactory operations or decide whether a token
deployment is valid.

## Protected Base Sepolia deployment

```text
[p]tokenfactory create
[p]tokenfactory deployment
[p]tokenfactory deployment <transaction_hash> <recipient_address>
[p]tokenfactory status
[p]tokenfactoryset deployment enable
[p]tokenfactoryset deployment pause
[p]tokenfactoryset deployment disable
[p]tokenfactoryset deployfactory
[p]tokenfactoryset verifyfactory
```

`tokenfactory create` opens a requester-bound card for the token name, symbol, fixed supply, and
decimals without provisioning a wallet. After saving the draft, the member chooses one of two
routes:

- **Deploy to Discord Wallet** resolves the member's Base Sepolia CryptoWallet smart account, uses it to
  sign the deployment, and sends the full supply there.
- **Deploy with External Wallet** opens a three-minute protected companion-page handoff for an injected
  EIP-1193 browser wallet such as MetaMask or Trust Wallet. The external wallet signs the pinned
  factory call and pays Base Sepolia gas. A blank recipient sends the full supply to the signer;
  an explicitly entered valid address receives it instead.

The external route never sends a private key, seed phrase, or browser wallet session to the bot or
website. After submission, the website reports the public transaction hash and recipient through
the one-time relay. The bot polls outward, verifies the deployment automatically, and DMs the
result. The page also supplies
`tokenfactory deployment <transaction_hash> <recipient_address>` as a fallback if the bot reloads
or automatic reporting is temporarily unavailable. Verification checks the successful transaction
destination, zero ETH value, exact calldata, request ID, factory record, token metadata, total
supply, and recipient balance before registration.

Member deployment remains disabled and emergency-paused until the bot owner runs
`tokenfactoryset deployment enable`. Enablement re-verifies the exact pinned factory on Base
Sepolia. `pause` immediately blocks both wallet routes without discarding configuration; `disable`
blocks them and disables the feature. Existing on-chain operations are never reversed by a pause.

The Discord Wallet route requires active CryptoWallet authorization and shows the active CDP
paymaster sponsorship, explicit gas limit, and zero native value before submission. After
submission, a bounded background watcher verifies the deployment, saves it to TokenFactory
history, registers it with CryptoWallet, and sends one private success card. Pending watcher state
survives reloads and restarts without submitting the operation again. If automatic confirmation
cannot finish or DMs are closed, the verified history or pending operation remains recoverable
through the normal deployment and history commands.

The external route does not require a CDP wallet profile or delegation; its review shows that the
signer pays its own testnet gas, the explicit gas limit, and zero native value. The reviewed terms
are bound to submission; changed or stale terms require a fresh review. Both routes use retry-safe
request IDs and the same pinned `createFixedSupplyToken` method. Live Discord guidance renders the
installation's configured command prefix; documentation continues to use `[p]`.

No mainnet network, arbitrary Solidity, arbitrary bytecode, arbitrary calldata, later minting
authority, upgrade path, administrator, or bot ownership is supported. The external route permits
only its documented signer-or-explicit-recipient choice, which is included in the exact calldata
verified after submission.

The pinned contract source and reproducible build live under [`contracts/`](contracts/).
