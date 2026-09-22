import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "contracts" / "clanker-v4-base-sepolia.json"
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


if __name__ == "__main__":
    unittest.main()
