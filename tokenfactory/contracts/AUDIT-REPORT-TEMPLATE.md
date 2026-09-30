# TokenFactory independent technical review report

This report supports only the tightly limited owner canary. It is not represented as a formal
security audit or approval for public or commercial mainnet use.

## Reviewer

- Reviewer or organization:
- Contact/public identity:
- Report date (UTC):
- Report URL or signature:
- Conflicts of interest:

## Exact scope

- Repository:
- Reviewed revision: `850a9d78a57ca6555d1f808d34fa0e6bf6a17356`
- Solidity source SHA-256:
- Package lock SHA-256:
- Factory artifact SHA-256:
- Compiler/version:
- Review exclusions:

## Reproduction

- Clean environment description:
- `npm ci` result:
- `npm test` result:
- Artifact diff result:
- Reproduced creation code hash:
- Reproduced runtime code hash:
- Reproduced predicted factory:
- Additional tools and versions:

## Security analysis

Address every numbered question in `AUDIT-HANDOFF.md`.

### Contract authority

### Fixed-supply and recipient guarantees

### Request-ID idempotency and conflicts

### CREATE2 and singleton deployment

### Registry and event integrity

### Input, denial-of-service, reentrancy, front-running, and cross-chain replay risks

### Compiler, dependencies, and reproducibility

### Off-chain approval and lifecycle boundary

## Findings

For every finding include:

- ID:
- Title:
- Severity: critical / high / medium / low / informational
- Status: open / accepted / fixed / verified
- Affected source/artifact:
- Preconditions:
- Impact:
- Evidence or reproduction:
- Recommendation:
- Resolution revision:
- Retest evidence:

State **No findings** if none were identified; do not omit this section.

## Final conclusion

Choose exactly one and explain any conditions:

- [ ] Approved for the single bot-owner Base mainnet factory and token canary under the documented limits.
- [ ] Not approved for Base mainnet deployment.

Conditions, limitations, and residual risks:

## Attestation

I reviewed the exact scope and reproduced the recorded artifacts. This report contains no wallet,
provider, Discord, relay, TOTP, or signing secrets.

- Reviewer name:
- Signature/attestation:
- Date:
