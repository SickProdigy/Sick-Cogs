"""Independent EIP-712 ClobAuth signer proof verification."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from eth_hash.auto import keccak

from .account_connection import AccountConnectionError, normalize_evm_address
from .onboarding import ProtectedOnboardingChallenge


CURVE_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
CURVE_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
CURVE_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
HALF_CURVE_N = CURVE_N // 2
SIGNATURE = re.compile(r"^0x[0-9a-fA-F]{130}$")
CLOB_AUTH_MESSAGE = "This message attests that I control the given wallet"
DOMAIN_TYPE = b"EIP712Domain(string name,string version,uint256 chainId)"
AUTH_TYPE = b"ClobAuth(address address,string timestamp,uint256 nonce,string message)"
Point = tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class SignerProofEvidence:
    signer_address: str
    proof_digest: str
    signed_at: int
    auth_nonce: int
    method: str = "eip712_clob_auth"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "signer_address",
            normalize_evm_address(self.signer_address, "signer_address"),
        )
        if not re.fullmatch(r"[0-9a-f]{64}", self.proof_digest):
            raise AccountConnectionError("Signer proof digest is invalid.")
        if self.signed_at <= 0 or not 0 <= self.auth_nonce < 2**256:
            raise AccountConnectionError("Signer proof timing or nonce is invalid.")
        if self.method != "eip712_clob_auth":
            raise AccountConnectionError("Signer proof method is invalid.")


def _word(value: int) -> bytes:
    if not 0 <= value < 2**256:
        raise AccountConnectionError("EIP-712 integer is outside uint256.")
    return value.to_bytes(32, "big")


def _address_word(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(normalize_evm_address(address, "address")[2:])


def clob_auth_digest(*, signer_address: str, timestamp: int, nonce: int) -> bytes:
    """Build the exact official ClobAuth EIP-712 digest for Polygon chain 137."""

    if timestamp <= 0:
        raise AccountConnectionError("Signer proof timestamp is invalid.")
    domain_separator = keccak(
        keccak(DOMAIN_TYPE)
        + keccak(b"ClobAuthDomain")
        + keccak(b"1")
        + _word(137)
    )
    struct_hash = keccak(
        keccak(AUTH_TYPE)
        + _address_word(signer_address)
        + keccak(str(timestamp).encode("ascii"))
        + _word(nonce)
        + keccak(CLOB_AUTH_MESSAGE.encode("ascii"))
    )
    return keccak(b"\x19\x01" + domain_separator + struct_hash)


def _point_add(left: Point, right: Point) -> Point:
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2 and (y1 + y2) % CURVE_P == 0:
        return None
    if left == right:
        if y1 == 0:
            return None
        slope = (3 * x1 * x1) * pow(2 * y1, -1, CURVE_P) % CURVE_P
    else:
        slope = (y2 - y1) * pow((x2 - x1) % CURVE_P, -1, CURVE_P) % CURVE_P
    x3 = (slope * slope - x1 - x2) % CURVE_P
    y3 = (slope * (x1 - x3) - y1) % CURVE_P
    return x3, y3


def _point_multiply(scalar: int, point: Point) -> Point:
    result = None
    addend = point
    if scalar < 0:
        raise AccountConnectionError("Curve scalar cannot be negative.")
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def recover_signer_address(digest: bytes, signature: str) -> str:
    """Recover a canonical low-S secp256k1 signer from one 65-byte signature."""

    if len(digest) != 32 or not isinstance(signature, str) or not SIGNATURE.fullmatch(signature):
        raise AccountConnectionError("Signer proof signature is invalid.")
    raw = bytes.fromhex(signature[2:])
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    recovery_id = raw[64]
    if recovery_id in {27, 28}:
        recovery_id -= 27
    if recovery_id not in {0, 1} or not 1 <= r < CURVE_N or not 1 <= s <= HALF_CURVE_N:
        raise AccountConnectionError("Signer proof signature is not canonical.")
    x = r
    if x >= CURVE_P:
        raise AccountConnectionError("Signer proof recovery point is invalid.")
    alpha = (pow(x, 3, CURVE_P) + 7) % CURVE_P
    beta = pow(alpha, (CURVE_P + 1) // 4, CURVE_P)
    if beta * beta % CURVE_P != alpha:
        raise AccountConnectionError("Signer proof recovery point is invalid.")
    y = beta if beta % 2 == recovery_id else CURVE_P - beta
    recovery_point = (x, y)
    if _point_multiply(CURVE_N, recovery_point) is not None:
        raise AccountConnectionError("Signer proof recovery point is invalid.")
    z = int.from_bytes(digest, "big")
    public_key = _point_multiply(
        pow(r, -1, CURVE_N),
        _point_add(
            _point_multiply(s, recovery_point),
            _point_multiply((-z) % CURVE_N, CURVE_G),
        ),
    )
    if public_key is None:
        raise AccountConnectionError("Signer proof public key is invalid.")
    encoded = public_key[0].to_bytes(32, "big") + public_key[1].to_bytes(32, "big")
    return "0x" + keccak(encoded)[-20:].hex()


def verify_clob_auth_proof(
    challenge: ProtectedOnboardingChallenge,
    *,
    signature: str,
    discord_user_id: int,
    now: int,
) -> SignerProofEvidence:
    """Verify and digest a transient proof without retaining its raw signature."""

    if discord_user_id != challenge.discord_user_id:
        raise AccountConnectionError("Signer proof belongs to a different Discord user.")
    if now < challenge.created_at or now >= challenge.expires_at:
        raise AccountConnectionError("Signer proof challenge is not current.")
    digest = clob_auth_digest(
        signer_address=challenge.signer_address,
        timestamp=challenge.created_at,
        nonce=challenge.auth_nonce,
    )
    recovered = recover_signer_address(digest, signature)
    if recovered != challenge.signer_address:
        raise AccountConnectionError("Signer proof does not match the onboarding signer.")
    return SignerProofEvidence(
        signer_address=recovered,
        proof_digest=hashlib.sha256(bytes.fromhex(signature[2:])).hexdigest(),
        signed_at=challenge.created_at,
        auth_nonce=challenge.auth_nonce,
    )
