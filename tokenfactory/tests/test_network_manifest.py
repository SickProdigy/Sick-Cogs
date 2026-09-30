import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ..network_manifest import TokenFactoryManifestError, load_network_manifest, mainnet_readiness
from ..tokenfactory import TokenFactory

class TokenFactoryNetworkManifestTests(unittest.TestCase):
    def test_mainnet_candidate_is_separate_and_fails_closed(self):
        testnet = load_network_manifest("base-sepolia")
        mainnet = load_network_manifest("base-mainnet")
        self.assertEqual(testnet["chainId"], 84532)
        self.assertEqual(mainnet["chainId"], 8453)
        self.assertEqual(testnet["predictedFactoryAddress"].lower(), mainnet["predictedFactoryAddress"].lower())
        self.assertIsNone(mainnet["factoryAddress"])
        self.assertEqual(mainnet["independentAudit"]["status"], "required")
        self.assertEqual(mainnet["authorization"], {"factoryDeployment": False, "ownerCanary": False, "memberDeployment": False})

    def test_mainnet_readiness_never_claims_authorization(self):
        status = mainnet_readiness()
        self.assertTrue(status["singleton_verified"])
        self.assertTrue(status["destination_empty"])
        self.assertFalse(status["factory_deployment_authorized"])
        self.assertFalse(status["owner_canary_authorized"])
        self.assertFalse(status["member_deployment_authorized"])

    def test_rejects_chain_drift_and_premature_authorization(self):
        original = load_network_manifest("base-mainnet")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "base-mainnet.json"
            for mutation, message in (
                ({"chainId": 84532}, "chain drift"),
                ({"authorization": {"factoryDeployment": True, "ownerCanary": False, "memberDeployment": False}}, "fail closed"),
            ):
                path.write_text(json.dumps({**original, **mutation}), encoding="utf-8")
                with patch("tokenfactory.network_manifest.MANIFEST_DIRECTORY", Path(directory)), self.assertRaisesRegex(TokenFactoryManifestError, message):
                    load_network_manifest("base-mainnet")

    def test_rejects_unknown_network(self):
        with self.assertRaisesRegex(TokenFactoryManifestError, "Unsupported"):
            load_network_manifest("base")

class TokenFactoryMainnetStatusCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_read_only_and_discloses_unmet_gates(self):
        ctx = SimpleNamespace(send=AsyncMock())
        await TokenFactory.tokenfactoryset_mainnet_status.callback(
            SimpleNamespace(), ctx
        )
        embed = ctx.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Network"], "Base mainnet (`8453`)")
        self.assertIn("required", fields["Required gates"])
        self.assertIn("unavailable", embed.footer.text)
