import copy
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from ..backend.auth import CLAIM_HANDOFF_LIFETIME_SECONDS, JwtAuthMixin, _key_id
from ..commands.authorization import WalletAuthorizationCommands
from ..commands.views import WalletAuthorizationView, WalletRevocationView


class _Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value


class _JwtHarness(JwtAuthMixin):
    def __init__(self, configuration, *, deployment_id="deployment", application_id=42):
        self._configuration = configuration
        self.config = SimpleNamespace(deployment_id=_Value(deployment_id))
        self.bot = SimpleNamespace(user=SimpleNamespace(id=application_id))

    async def jwt_configuration(self):
        return self._configuration


def _profile():
    return {
        "profile_id": "profile-7",
        "provider_user_id": "profile-7",
        "discord_user_id": 7,
        "accounts": [{
            "network": "base-sepolia",
            "address": "0x7930fB6E9853B3835Cf047f36855993cb82d4387",
        }],
    }


def _interaction(user_id=7):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )


class AuthorizationViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_and_revoke_only_controls_are_distinct(self):
        active = WalletAuthorizationView(object(), 7, _profile())
        revoke = WalletRevocationView(object(), 7, _profile())
        self.assertEqual(
            [item.label for item in active.children],
            ["Renew authorization", "Revoke authorization", "Cancel"],
        )
        self.assertEqual(
            [item.label for item in revoke.children],
            ["Revoke authorization", "Cancel"],
        )

    async def test_active_card_explains_deliberate_renewal(self):
        expiry = datetime(2026, 9, 5, tzinfo=timezone.utc)
        embed = WalletAuthorizationCommands._active_authorization_embed(
            {"address": _profile()["accounts"][0]["address"]}, expiry
        )
        options = next(field.value for field in embed.fields if field.name == "Options")
        self.assertIn("deliberately renew", options)
        self.assertIn("Revoke authorization", options)

    async def test_renewal_rechecks_status_and_preserves_active_grant(self):
        provider = SimpleNamespace(
            get_delegation_status=AsyncMock(
                return_value={"active": True, "expires_at": "2026-09-05T00:00:00Z"}
            )
        )
        cog = SimpleNamespace(
            wallet_provider=provider,
            send_authorization_link=AsyncMock(return_value=1_800_000_000),
        )
        view = WalletAuthorizationView(cog, 7, _profile())
        interaction = _interaction()
        await WalletAuthorizationCommands.renew_authorization_interaction(
            cog, interaction, view
        )
        cog.send_authorization_link.assert_awaited_once_with(
            interaction.user, view.profile, renewal=True
        )
        renewal = next(
            item for item in view.children if item.label == "Renew authorization"
        )
        self.assertTrue(renewal.disabled)
        self.assertIn(
            "remains active and unchanged", interaction.followup.send.await_args.args[0]
        )


class AuthorizationHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.configuration = {
            "issuer": "https://wallet.example.test",
            "audience": "project-id",
            "kid": _key_id(self.key),
            "private_key": self.key,
        }

    async def test_handoff_is_signed_and_bound_to_wallet_identity(self):
        harness = _JwtHarness(self.configuration)
        before = int(time.time())
        token, expires_at = await harness.create_authorization_handoff(7, _profile())
        claims = jwt.decode(
            token,
            self.key.public_key(),
            algorithms=["ES256"],
            audience="project-id",
            issuer="https://wallet.example.test",
        )
        self.assertEqual(claims["sub"], "profile-7")
        self.assertEqual(claims["sickwallet_discord_user"], "7")
        self.assertEqual(claims["sickwallet_application"], "42")
        self.assertEqual(claims["sickwallet_deployment"], "deployment")
        self.assertEqual(claims["sickwallet_purpose"], "authorize")
        self.assertEqual(
            claims["sickwallet_address"],
            "0x7930fB6E9853B3835Cf047f36855993cb82d4387",
        )
        self.assertGreaterEqual(expires_at, before + CLAIM_HANDOFF_LIFETIME_SECONDS)
        self.assertLessEqual(expires_at, int(time.time()) + CLAIM_HANDOFF_LIFETIME_SECONDS)

    async def test_handoff_rejects_mismatched_stored_identity(self):
        cases = []
        wrong_provider = copy.deepcopy(_profile())
        wrong_provider["provider_user_id"] = "other-profile"
        cases.append(wrong_provider)
        wrong_discord = copy.deepcopy(_profile())
        wrong_discord["discord_user_id"] = 8
        cases.append(wrong_discord)
        missing_account = copy.deepcopy(_profile())
        missing_account["accounts"] = []
        cases.append(missing_account)
        harness = _JwtHarness(self.configuration)
        for profile in cases:
            with self.subTest(profile=profile):
                with self.assertRaises(RuntimeError):
                    await harness.create_authorization_handoff(7, profile)


if __name__ == "__main__":
    unittest.main()
