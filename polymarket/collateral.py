"""Exact non-executable collateral and approval disclosures for Polygon Polymarket."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .account_connection import normalize_evm_address
from .production_manifest import PolymarketProductionManifest, POLYMARKET_PRODUCTION_MANIFEST


class CollateralPlanError(ValueError):
    """Raised when a collateral disclosure cannot be bounded exactly."""


@dataclass(frozen=True, slots=True)
class ApprovalDisclosure:
    asset: str
    token: str
    standard: str
    spender: str
    amount_base_units: int | None
    operator_approved: bool | None
    purpose: str
    revoke_value: str

    def __post_init__(self):
        object.__setattr__(self, "token", normalize_evm_address(self.token, "token"))
        object.__setattr__(self, "spender", normalize_evm_address(self.spender, "spender"))
        if self.standard == "ERC-20":
            if self.amount_base_units is None or self.amount_base_units <= 0:
                raise CollateralPlanError("ERC-20 approval requires an exact positive amount.")
            if self.operator_approved is not None or self.revoke_value != "0":
                raise CollateralPlanError("ERC-20 revocation must set allowance to zero.")
        elif self.standard == "ERC-1155":
            if self.amount_base_units is not None or self.operator_approved is not True:
                raise CollateralPlanError("ERC-1155 approval must explicitly enable the operator.")
            if self.revoke_value != "false":
                raise CollateralPlanError("ERC-1155 revocation must disable the operator.")
        else:
            raise CollateralPlanError("Unsupported approval standard.")


@dataclass(frozen=True, slots=True)
class CollateralActionPlan:
    action: str
    source_asset: str
    source_token: str
    destination_asset: str
    destination_token: str
    amount_base_units: int
    account_wallet: str
    contract: str
    function: str
    approvals: tuple[ApprovalDisclosure, ...]
    executable: bool = False

    def __post_init__(self):
        if self.action not in {"wrap", "unwrap", "trading"}:
            raise CollateralPlanError("Unsupported collateral action.")
        for field in ("source_token", "destination_token", "account_wallet", "contract"):
            object.__setattr__(self, field, normalize_evm_address(getattr(self, field), field))
        if self.amount_base_units <= 0:
            raise CollateralPlanError("Collateral amount must be positive.")
        if self.executable:
            raise CollateralPlanError("Collateral plans must remain non-executable.")
        if not self.approvals:
            raise CollateralPlanError("Collateral plan requires explicit approval disclosure.")

    @property
    def display_amount(self) -> str:
        return format(Decimal(self.amount_base_units) / Decimal(10**6), "f")


def _base_units(amount: str) -> int:
    try:
        value = Decimal(amount)
    except (InvalidOperation, ValueError) as exc:
        raise CollateralPlanError("Amount must be a decimal.") from exc
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -6:
        raise CollateralPlanError("Amount must be positive with at most six decimals.")
    return int(value * 10**6)


def collateral_plan(
    action: str, amount: str, account_wallet: str,
    manifest: PolymarketProductionManifest = POLYMARKET_PRODUCTION_MANIFEST,
    *, negative_risk: bool = False,
) -> CollateralActionPlan:
    units = _base_units(amount)
    wallet = normalize_evm_address(account_wallet, "account_wallet")
    choice = action.casefold()
    if choice == "wrap":
        approval = ApprovalDisclosure(
            "USDC.e", manifest.usdce_token, "ERC-20", manifest.collateral_onramp,
            units, None, "Allow this exact USDC.e amount to be wrapped", "0",
        )
        return CollateralActionPlan(
            "wrap", "USDC.e", manifest.usdce_token, "pUSD", manifest.collateral_token,
            units, wallet, manifest.collateral_onramp, "wrap(USDC.e, account wallet, amount)",
            (approval,),
        )
    if choice == "unwrap":
        approval = ApprovalDisclosure(
            "pUSD", manifest.collateral_token, "ERC-20", manifest.collateral_offramp,
            units, None, "Allow this exact pUSD amount to be unwrapped", "0",
        )
        return CollateralActionPlan(
            "unwrap", "pUSD", manifest.collateral_token, "USDC.e", manifest.usdce_token,
            units, wallet, manifest.collateral_offramp, "unwrap(USDC.e, account wallet, amount)",
            (approval,),
        )
    if choice == "trading":
        exchange = manifest.neg_risk_exchange if negative_risk else manifest.ctf_exchange
        approvals = (
            ApprovalDisclosure(
                "pUSD", manifest.collateral_token, "ERC-20", exchange, units, None,
                "Bound collateral available to the selected exchange", "0",
            ),
            ApprovalDisclosure(
                "Outcome tokens", manifest.conditional_tokens, "ERC-1155", exchange, None,
                True, "Permit the selected exchange to transfer outcome tokens", "false",
            ),
        )
        return CollateralActionPlan(
            "trading", "pUSD", manifest.collateral_token, "Outcome tokens",
            manifest.conditional_tokens, units, wallet, exchange,
            "approve exact pUSD and set outcome-token operator", approvals,
        )
    raise CollateralPlanError("Choose wrap, unwrap, standard, or negative-risk.")
