"""Pinned, non-executable Polymarket production integration contract."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolymarketProductionManifest:
    """Official production facts that must fail closed when they drift."""

    schema_version: int = 1
    chain_id: int = 137
    network_key: str = "polygon"
    collateral_symbol: str = "pUSD"
    collateral_decimals: int = 6
    collateral_token: str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    collateral_onramp: str = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
    collateral_offramp: str = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"
    ctf_exchange: str = "0xE111180000d2663C0091e4f400237545B87B996B"
    neg_risk_exchange: str = "0xe2222d279d744050d28e00520010520000310F59"
    conditional_tokens: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    deposit_wallet_factory: str = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
    gamma_api: str = "https://gamma-api.polymarket.com"
    clob_api: str = "https://clob.polymarket.com"
    relayer_api: str = "https://relayer-v2.polymarket.com"
    supported_wallet_types: tuple[str, ...] = (
        "EOA", "POLY_PROXY", "GNOSIS_SAFE", "DEPOSIT_WALLET",
    )
    default_new_wallet_type: str = "DEPOSIT_WALLET"
    signer_and_wallet_are_distinct: bool = True
    session_keys_documented: bool = True
    execution_enabled: bool = False
    executable_capabilities: tuple[str, ...] = ()


POLYMARKET_PRODUCTION_MANIFEST = PolymarketProductionManifest()


def validate_polymarket_production_manifest(
    manifest: PolymarketProductionManifest = POLYMARKET_PRODUCTION_MANIFEST,
) -> tuple[str, ...]:
    """Return every contract drift reason; an empty tuple is the only passing state."""

    errors = []
    expected_addresses = {
        "collateral_token": "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
        "collateral_onramp": "0x93070a847efef7f70739046a929d47a521f5b8ee",
        "collateral_offramp": "0x2957922eb93258b93368531d39facca3b4dc5854",
        "ctf_exchange": "0xe111180000d2663c0091e4f400237545b87b996b",
        "neg_risk_exchange": "0xe2222d279d744050d28e00520010520000310f59",
        "conditional_tokens": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",
        "deposit_wallet_factory": "0x00000000000fb5c9adea0298d729a0cb3823cc07",
    }
    if manifest.schema_version != 1:
        errors.append("unsupported manifest schema")
    if manifest.chain_id != 137 or manifest.network_key != "polygon":
        errors.append("Polygon network binding mismatch")
    if manifest.collateral_symbol != "pUSD" or manifest.collateral_decimals != 6:
        errors.append("pUSD collateral binding mismatch")
    for field, expected in expected_addresses.items():
        if str(getattr(manifest, field, "")).lower() != expected:
            errors.append(f"{field} address mismatch")
    if (manifest.gamma_api, manifest.clob_api, manifest.relayer_api) != (
        "https://gamma-api.polymarket.com",
        "https://clob.polymarket.com",
        "https://relayer-v2.polymarket.com",
    ):
        errors.append("official API endpoint mismatch")
    if set(manifest.supported_wallet_types) != {
        "EOA", "POLY_PROXY", "GNOSIS_SAFE", "DEPOSIT_WALLET",
    }:
        errors.append("wallet type contract mismatch")
    if manifest.default_new_wallet_type != "DEPOSIT_WALLET":
        errors.append("new-account wallet type mismatch")
    if not manifest.signer_and_wallet_are_distinct:
        errors.append("signer/account-wallet separation is disabled")
    if not manifest.session_keys_documented:
        errors.append("session-key contract is missing")
    if manifest.execution_enabled or manifest.executable_capabilities:
        errors.append("Polymarket execution must remain disabled")
    return tuple(errors)
