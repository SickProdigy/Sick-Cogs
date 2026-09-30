import json
import re
from pathlib import Path
from typing import Any

MANIFEST_DIRECTORY = Path(__file__).parent / "contracts" / "manifests"
NETWORK_MANIFEST_FILES = {"base-sepolia": "base-sepolia.json", "base-mainnet": "base-mainnet.json"}
EXPECTED_CHAIN_IDS = {"base-sepolia": 84532, "base-mainnet": 8453}
HASH_RE = re.compile(r"^(?:0x)?[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

class TokenFactoryManifestError(RuntimeError):
    """A pinned TokenFactory network manifest is missing or unsafe."""

def load_network_manifest(network: str) -> dict[str, Any]:
    key = str(network or "").strip().lower()
    filename = NETWORK_MANIFEST_FILES.get(key)
    if filename is None:
        raise TokenFactoryManifestError("Unsupported TokenFactory network manifest.")
    try:
        manifest = json.loads((MANIFEST_DIRECTORY / filename).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise TokenFactoryManifestError(f"The pinned {key} TokenFactory manifest could not be loaded.") from exc
    if manifest.get("network") != key:
        raise TokenFactoryManifestError("TokenFactory manifest network drift detected.")
    if manifest.get("chainId") != EXPECTED_CHAIN_IDS[key]:
        raise TokenFactoryManifestError("TokenFactory manifest chain drift detected.")
    for field in ("sourceSha256", "packageLockSha256", "factoryCreationCodeHash", "factoryRuntimeCodeHash", "singletonFactoryRuntimeCodeHash"):
        if not HASH_RE.fullmatch(str(manifest.get(field) or "").lower()):
            raise TokenFactoryManifestError(f"TokenFactory manifest has an invalid {field}.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("sourceRevision") or "")):
        raise TokenFactoryManifestError("TokenFactory manifest has an invalid source revision.")
    if manifest.get("createFixedSupplyTokenSelector") != "0x8b08cf96" or manifest.get("deploymentSelector") != "0xb40a41ce":
        raise TokenFactoryManifestError("TokenFactory manifest selector drift detected.")
    if not HASH_RE.fullmatch(str(manifest.get("fixedSupplyTokenCreatedTopic") or "")):
        raise TokenFactoryManifestError("TokenFactory manifest event topic is invalid.")
    for field in ("singletonFactory", "predictedFactoryAddress"):
        if not ADDRESS_RE.fullmatch(str(manifest.get(field) or "")):
            raise TokenFactoryManifestError(f"TokenFactory manifest has an invalid {field}.")
    configured = manifest.get("factoryAddress")
    if configured is not None and str(configured).lower() != str(manifest["predictedFactoryAddress"]).lower():
        raise TokenFactoryManifestError("TokenFactory manifest factory address does not match its derivation.")
    if key == "base-mainnet":
        authorization = manifest.get("authorization")
        audit = manifest.get("independentAudit")
        if not isinstance(authorization, dict) or any(authorization.get(item) is not False for item in ("factoryDeployment", "ownerCanary", "memberDeployment")):
            raise TokenFactoryManifestError("Base mainnet TokenFactory authorization must fail closed.")
        if not isinstance(audit, dict) or audit.get("status") == "complete":
            raise TokenFactoryManifestError("Base mainnet audit state is invalid or prematurely complete.")
        if configured is not None:
            raise TokenFactoryManifestError("Base mainnet factory is unexpectedly recorded as deployed.")
    return manifest

def mainnet_readiness() -> dict[str, Any]:
    manifest = load_network_manifest("base-mainnet")
    observations = manifest.get("observations", {})
    return {
        "network": manifest["network"], "chain_id": manifest["chainId"], "status": manifest["status"],
        "predicted_factory_address": manifest["predictedFactoryAddress"],
        "singleton_verified": bool(observations.get("singletonCodeMatched")),
        "destination_empty": bool(observations.get("factoryDestinationEmpty")),
        "independent_audit": str(manifest.get("independentAudit", {}).get("status") or "missing"),
        "factory_deployment_authorized": False, "owner_canary_authorized": False,
        "member_deployment_authorized": False,
    }
