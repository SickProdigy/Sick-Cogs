"""Compose independently verified evidence into one protected onboarding result."""

from .account_connection import AccountConnectionError
from .identity_verifier import AccountRelationshipEvidence
from .onboarding import ProtectedOnboardingChallenge, ProtectedOnboardingResult
from .security_policy import EligibilityAttestation
from .signer_proof import SignerProofEvidence


def finalize_onboarding_evidence(
    challenge: ProtectedOnboardingChallenge,
    *,
    signer_proof: SignerProofEvidence,
    account_relationship: AccountRelationshipEvidence,
    eligibility: EligibilityAttestation,
    discord_user_id: int,
    now: int,
) -> ProtectedOnboardingResult:
    """Require three exact evidence sources before producing a consumable result."""

    if discord_user_id != challenge.discord_user_id:
        raise AccountConnectionError("Onboarding evidence belongs to a different Discord user.")
    if now < challenge.created_at or now >= challenge.expires_at:
        raise AccountConnectionError("Onboarding evidence challenge is not current.")
    if (
        signer_proof.signer_address != challenge.signer_address
        or signer_proof.signed_at != challenge.created_at
        or signer_proof.auth_nonce != challenge.auth_nonce
    ):
        raise AccountConnectionError("Signer evidence does not match the challenge.")
    if (
        account_relationship.signer_address != challenge.signer_address
        or account_relationship.account_wallet_address
        != challenge.account_wallet_address
        or account_relationship.wallet_type is not challenge.wallet_type
        or account_relationship.chain_id != challenge.chain_id
    ):
        raise AccountConnectionError("Account relationship evidence does not match the challenge.")
    eligibility.require_current(discord_user_id=discord_user_id, now=now)
    if not challenge.created_at <= eligibility.checked_at < challenge.expires_at:
        raise AccountConnectionError("Eligibility evidence does not belong to the challenge.")
    return ProtectedOnboardingResult(
        challenge_fingerprint=challenge.fingerprint,
        discord_user_id=discord_user_id,
        signer_address=challenge.signer_address,
        account_wallet_address=challenge.account_wallet_address,
        wallet_type=challenge.wallet_type,
        proof_method=signer_proof.method,
        proof_digest=signer_proof.proof_digest,
        relationship_source=account_relationship.source,
        relationship_evidence_digest=account_relationship.evidence_digest,
        eligibility=eligibility,
        verified_at=eligibility.checked_at,
    )
