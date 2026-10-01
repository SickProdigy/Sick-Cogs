import json
import re
import unittest
from pathlib import Path

from ..helpers import keccak256


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "contracts" / "clanker-v4-base-sepolia.json"
MAINNET_CANDIDATE_PATH = ROOT / "contracts" / "clanker-v4-base-mainnet-candidate.json"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ClankerContractManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_is_strictly_base_sepolia(self):
        self.assertEqual(self.manifest["status"], "prototype-pinned")
        self.assertEqual(self.manifest["network"], "base-sepolia")
        self.assertEqual(self.manifest["chainId"], 84532)
        self.assertNotIn("mainnet", json.dumps(self.manifest).lower())

    def test_official_sdk_revision_is_immutable(self):
        sdk = self.manifest["sdk"]
        self.assertEqual(sdk["repository"], "https://github.com/clanker-devco/clanker-sdk")
        self.assertEqual(sdk["tag"], "v4.2.19")
        self.assertRegex(sdk["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(len(sdk["sourceHashes"]), 3)
        for digest in sdk["sourceHashes"].values():
            self.assertRegex(digest, HASH_RE)

    def test_only_reviewed_deploy_entrypoint_is_pinned(self):
        factory = self.manifest["factory"]
        self.assertEqual(factory["address"].lower(), "0xe85a59c628f7d27878aceb4bf3b35733630083a9")
        self.assertEqual(factory["function"], "deployToken")
        self.assertEqual(factory["selector"], "0xdf40224a")
        self.assertEqual(factory["stateMutability"], "payable")
        self.assertEqual(
            factory["canonicalSignature"],
            "deployToken(((address,string,string,bytes32,string,string,string,uint256),"
            "(address,address,int24,int24,bytes),"
            "(address,address[],address[],uint16[],int24[],int24[],uint16[],bytes),"
            "(address,bytes),(address,uint256,uint16,bytes)[]))",
        )
        self.assertRegex(factory["runtimeCodeSha256"], HASH_RE)

    def test_all_pinned_addresses_are_nonzero_evm_addresses(self):
        addresses = [self.manifest["factory"]["address"]]
        addresses.extend(self.manifest["relatedContracts"].values())
        self.assertEqual(len(addresses), len({value.lower() for value in addresses}))
        for address in addresses:
            self.assertRegex(address, ADDRESS_RE)
            self.assertNotEqual(int(address[2:], 16), 0)

    def test_manifest_is_complete_json(self):
        raw = MANIFEST_PATH.read_bytes()
        self.assertEqual(
            set(self.manifest),
            {"status", "network", "chainId", "protocol", "sdk", "factory", "relatedContracts"},
        )
        self.assertTrue(raw.endswith(b"\n"))

    def test_mainnet_operation_allowlist_pins_signatures_selectors_and_events(self):
        candidate = json.loads(MAINNET_CANDIDATE_PATH.read_text(encoding="utf-8"))
        operations = candidate["operationAllowlist"]
        self.assertEqual(set(operations), {
            "launch", "creatorBuyIn", "rewardDiscovery", "rewardCollection",
            "rewardConfiguration", "treasuryClaim", "vaultDiscovery", "vaultClaim",
            "airdropDiscovery", "airdropClaim",
        })
        self.assertIs(operations["creatorBuyIn"]["standaloneCallAllowed"], False)
        for name, operation in operations.items():
            if name == "creatorBuyIn":
                continue
            expected_selector = "0x" + keccak256(operation["signature"].encode("ascii"))[:4].hex()
            self.assertEqual(operation["selector"], expected_selector, name)
            target = operation["target"]
            self.assertTrue(target == "factory" or target in candidate["contracts"], name)
            if "successEvent" in operation:
                expected_topic = "0x" + keccak256(operation["successEvent"].encode("ascii")).hex()
                self.assertEqual(operation["successTopic"], expected_topic, name)
        self.assertIn("updateRewardRecipient(address,uint256,address)", candidate["explicitlyExcludedFunctions"])
        self.assertIn("withdrawETH(address)", candidate["explicitlyExcludedFunctions"])

    def test_mainnet_candidate_is_non_executable_audit_evidence(self):
        candidate = json.loads(MAINNET_CANDIDATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(candidate["status"], "audit-candidate-read-only")
        self.assertIs(candidate["executionEnabled"], False)
        self.assertEqual(candidate["network"], "base-mainnet")
        self.assertEqual(candidate["chainId"], 8453)
        self.assertIs(candidate["rpcVerification"]["allRuntimeCodeMatched"], True)
        self.assertEqual(len(candidate["rpcVerification"]["sources"]), 2)
        self.assertIn("owner-configured treasury", candidate["rewardMutability"]["platformGuarantee"])
        self.assertNotIn(
            MAINNET_CANDIDATE_PATH.name,
            (Path(__file__).parents[1] / "models.py").read_text(encoding="utf-8"),
        )
        pinned = [candidate["factory"], *candidate["contracts"].values()]
        self.assertTrue(all(item["runtimeCodeBytes"] > 0 for item in pinned))
        self.assertTrue(all(HASH_RE.fullmatch(item["runtimeCodeSha256"]) for item in pinned))


if __name__ == "__main__":
    unittest.main()
