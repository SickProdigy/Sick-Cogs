import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load("cryptowallet.core.clanker", ROOT / "core" / "clanker.py")
provider = load("cryptowallet.providers.clanker", ROOT / "providers" / "clanker.py")


WALLET = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TREASURY = "0x1111111111111111111111111111111111111111"
WETH = "0x4200000000000000000000000000000000000006"


def intent(**overrides):
    values = dict(
        intent_id="0x" + "12" * 32, deployment_id="deployment-1",
        discord_application_id=42, guild_id=100, discord_user_id=7,
        profile_id="profile-7", wallet_address=WALLET, token_admin=WALLET,
        name="Test Clanker", symbol="CLANK", image="https://example.test/clank.png",
        metadata={"description": "test", "socialMediaUrls": []},
        context={"interface": "SickGamingBot", "platform": "discord"},
        pool=core.ClankerPool(WETH, -230400, 200, (core.ClankerPoolPosition(-230400, -120000, 10_000),)),
        rewards=(core.ClankerReward(WALLET, WALLET, 8_000), core.ClankerReward(TREASURY, TREASURY, 2_000)),
        created_at=1_700_000_000, expires_at=1_700_000_600,
    )
    values.update(overrides)
    return core.ClankerDeploymentIntent.create(**values)


class ClankerProviderTests(unittest.TestCase):
    def test_builds_pinned_call_and_is_deterministic(self):
        launch = intent(vault=core.ClankerVault(WALLET, 10, 604800), airdrop=core.ClankerAirdrop(WALLET, "0x" + "ab" * 32, 250_000_000, 86400))
        data = provider.clanker_deployment_calldata(launch)
        self.assertTrue(data.startswith("0x" + provider.DEPLOY_TOKEN_SELECTOR))
        self.assertEqual(data, provider.clanker_deployment_calldata(launch))
        self.assertEqual(hashlib.sha256(data.encode()).hexdigest(), "269141abacf2e4e53e8b69edea1d0c78ed76ce93498de8e61ded1d82377b9a45")
        provider.validate_clanker_deployment_call(launch, to=core.CLANKER_FACTORY, value=0, data=data)

    def test_reward_collection_is_pinned_to_one_token(self):
        token = "0x2222222222222222222222222222222222222222"
        call = provider.clanker_collect_rewards_call(token)
        self.assertEqual(call["to"], provider.LP_LOCKER)
        self.assertEqual(call["value"], 0)
        self.assertEqual(call["data"][:10], "0x5763dbd0")
        provider.validate_clanker_collect_rewards_call(token, **call)
        with self.assertRaises(ValueError):
            provider.validate_clanker_collect_rewards_call(WALLET, **call)

    def test_treasury_claim_is_pinned_to_owner_and_asset(self):
        call = provider.clanker_claim_call(WALLET, WETH)
        self.assertEqual(call["to"], provider.FEE_LOCKER)
        self.assertEqual(call["value"], 0)
        self.assertEqual(call["data"][:10], "0x21c0b342")
        provider.validate_clanker_claim_call(WALLET, WETH, **call)
        with self.assertRaises(ValueError):
            provider.validate_clanker_claim_call(TREASURY, WETH, **call)

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
