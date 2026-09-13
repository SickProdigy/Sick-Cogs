import hashlib
import unittest


from .. import models as core
from .. import operation as provider


WALLET = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TREASURY = "0x1111111111111111111111111111111111111111"
WETH = "0x4200000000000000000000000000000000000006"


def intent(**overrides):
    values = dict(
        launch_id="0x" + "12" * 32, guild_id=100, requester_id=7,
        token_admin=WALLET,
        name="Test Clanker", symbol="CLANK", image="https://example.test/clank.png",
        metadata={"description": "test", "socialMediaUrls": []},
        context={"interface": "SickGamingBot", "platform": "discord"},
        pool=core.ClankerPool(WETH, -230400, 200, (core.ClankerPoolPosition(-230400, -120000, 10_000),)),
        rewards=(core.ClankerReward(WALLET, WALLET, 8_000), core.ClankerReward(TREASURY, TREASURY, 2_000)),
        created_at=1_700_000_000, expires_at=1_700_000_600,
    )
    values.update(overrides)
    return core.ClankerLaunchIntent.create(**values)


class ClankerProviderTests(unittest.TestCase):
    def test_builds_pinned_call_and_is_deterministic(self):
        launch = intent(vault=core.ClankerVault(WALLET, 10, 604800), airdrop=core.ClankerAirdrop(WALLET, "0x" + "ab" * 32, 250_000_000, 86400))
        data = provider.clanker_deployment_calldata(launch)
        self.assertTrue(data.startswith("0x" + provider.DEPLOY_TOKEN_SELECTOR))
        self.assertEqual(data, provider.clanker_deployment_calldata(launch))
        # Reference hash captured from the pinned official SDK v4.2.19 converter.
        self.assertEqual(hashlib.sha256(data.encode()).hexdigest(), "269141abacf2e4e53e8b69edea1d0c78ed76ce93498de8e61ded1d82377b9a45")
        provider.validate_clanker_deployment_call(launch, to=core.CLANKER_FACTORY, value=0, data=data)
        operation = provider.clanker_deployment_operation(launch)
        self.assertEqual(operation.chain_id, 84532)
        self.assertEqual(operation.to.lower(), core.CLANKER_FACTORY.lower())
        self.assertEqual(operation.value, 0)
        self.assertEqual(operation.data, data)
        self.assertEqual(operation.payload_hash, launch.payload_hash)

    def test_rejects_changed_target_value_or_data(self):
        launch = intent()
        data = provider.clanker_deployment_calldata(launch)
        for changed in (
            {"to": TREASURY, "value": 0, "data": data},
            {"to": core.CLANKER_FACTORY, "value": 1, "data": data},
            {"to": core.CLANKER_FACTORY, "value": 0, "data": data[:-1] + ("0" if data[-1] != "0" else "1")},
        ):
            with self.assertRaises(ValueError):
                provider.validate_clanker_deployment_call(launch, **changed)


if __name__ == "__main__":
    unittest.main()
