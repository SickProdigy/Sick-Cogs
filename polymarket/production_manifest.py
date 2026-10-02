"""Pinned, non-executable Polymarket production integration contract."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolymarketProductionManifest:
    """Official production facts that must fail closed when they drift."""

    schema_version: int = 6
    chain_id: int = 137
    network_key: str = "polygon"
    collateral_symbol: str = "pUSD"
    collateral_decimals: int = 6
    usdce_token: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    collateral_token: str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    collateral_onramp: str = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
    collateral_offramp: str = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"
    ctf_exchange: str = "0xE111180000d2663C0091e4f400237545B87B996B"
    neg_risk_exchange: str = "0xe2222d279d744050d28e00520010520000310F59"
    exchange_v3: str = "0xe3333700cA9d93003F00f0F71f8515005F6c00Aa"
    protocol_v2_router: str = "0x12121212006e4CD160D18e3f00711DA5c3372600"
    position_manager: str = "0x006F54F7f9A22e0000CC2AB60031000000ae9fEF"
    conditional_tokens: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    deposit_wallet_factory: str = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
    deposit_wallet_beacon: str = "0x7A18EDfe055488A3128f01F563e5B479D92ffc3a"
    deposit_wallet_implementation: str = "0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB"
    proxy_wallet_factory: str = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
    proxy_wallet_implementation: str = "0x44e999d5c2F66Ef0861317f9A4805AC2e90aEB4f"
    safe_wallet_factory: str = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
    safe_init_code_hash: str = "0x2bce2127ff07fb632d16c8347c4ebf501f4841168bed00d9e6ef715ddb6fcecf"
    gamma_api: str = "https://gamma-api.polymarket.com"
    clob_api: str = "https://clob.polymarket.com"
    data_api: str = "https://data-api.polymarket.com"
    relayer_api: str = "https://relayer-v2.polymarket.com"
    bridge_api: str = "https://bridge.polymarket.com"
    polygon_rpc: str = "https://polygon.drpc.org"
    supported_wallet_types: tuple[str, ...] = (
        "EOA", "POLY_PROXY", "GNOSIS_SAFE", "DEPOSIT_WALLET",
    )
    default_new_wallet_type: str = "DEPOSIT_WALLET"
    signer_and_wallet_are_distinct: bool = True
    session_keys_documented: bool = True
    deposit_wallet_order_signature_type: int = 3
    order_protocol_versions: tuple[str, ...] = ("2", "3")
    order_signature_scheme: str = "ERC-7739_SESSION_KEY"
    execution_enabled: bool = False
    executable_capabilities: tuple[str, ...] = ()


POLYMARKET_PRODUCTION_MANIFEST = PolymarketProductionManifest()


def validate_polymarket_production_manifest(
    manifest: PolymarketProductionManifest = POLYMARKET_PRODUCTION_MANIFEST,
) -> tuple[str, ...]:
    """Return every contract drift reason; an empty tuple is the only passing state."""

    errors = []
    expected_addresses = {
        "usdce_token": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
        "collateral_token": "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb",
        "collateral_onramp": "0x93070a847efef7f70739046a929d47a521f5b8ee",
        "collateral_offramp": "0x2957922eb93258b93368531d39facca3b4dc5854",
        "ctf_exchange": "0xe111180000d2663c0091e4f400237545b87b996b",
        "neg_risk_exchange": "0xe2222d279d744050d28e00520010520000310f59",
        "exchange_v3": "0xe3333700ca9d93003f00f0f71f8515005f6c00aa",
        "protocol_v2_router": "0x12121212006e4cd160d18e3f00711da5c3372600",
        "position_manager": "0x006f54f7f9a22e0000cc2ab60031000000ae9fef",
        "conditional_tokens": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",
        "deposit_wallet_factory": "0x00000000000fb5c9adea0298d729a0cb3823cc07",
        "deposit_wallet_beacon": "0x7a18edfe055488a3128f01f563e5b479d92ffc3a",
        "deposit_wallet_implementation": "0x58ca52ebe0dadfdf531cde7062e76746de4db1eb",
        "proxy_wallet_factory": "0xab45c5a4b0c941a2f231c04c3f49182e1a254052",
        "proxy_wallet_implementation": "0x44e999d5c2f66ef0861317f9a4805ac2e90aeb4f",
        "safe_wallet_factory": "0xaacfeea03eb1561c4e67d661e40682bd20e3541b",
    }
    if manifest.schema_version != 6:
        errors.append("unsupported manifest schema")
    if manifest.chain_id != 137 or manifest.network_key != "polygon":
        errors.append("Polygon network binding mismatch")
    if manifest.collateral_symbol != "pUSD" or manifest.collateral_decimals != 6:
        errors.append("pUSD collateral binding mismatch")
    for field, expected in expected_addresses.items():
        if str(getattr(manifest, field, "")).lower() != expected:
            errors.append(f"{field} address mismatch")
    if manifest.safe_init_code_hash.lower() != "0x2bce2127ff07fb632d16c8347c4ebf501f4841168bed00d9e6ef715ddb6fcecf":
        errors.append("Safe init-code hash mismatch")
    if (
        manifest.gamma_api, manifest.clob_api, manifest.data_api,
        manifest.relayer_api, manifest.bridge_api,
    ) != (
        "https://gamma-api.polymarket.com",
        "https://clob.polymarket.com",
        "https://data-api.polymarket.com",
        "https://relayer-v2.polymarket.com",
        "https://bridge.polymarket.com",
    ):
        errors.append("official API endpoint mismatch")
    if manifest.polygon_rpc != "https://polygon.drpc.org":
        errors.append("official Polygon RPC mismatch")
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
    if (
        manifest.deposit_wallet_order_signature_type != 3
        or manifest.order_protocol_versions != ("2", "3")
        or manifest.order_signature_scheme != "ERC-7739_SESSION_KEY"
    ):
        errors.append("Deposit Wallet order-signature contract mismatch")
    if manifest.execution_enabled or manifest.executable_capabilities:
        errors.append("Polymarket execution must remain disabled")
    return tuple(errors)
