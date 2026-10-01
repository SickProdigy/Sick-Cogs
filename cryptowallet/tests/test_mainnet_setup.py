import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..commands.authorization import WalletAuthorizationCommands
from ..commands.views import WalletMainnetSetupView


class _Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value


class MainnetSetupDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mainnet_missing_terms_sends_one_guided_card(self):
        token = "x" * 40
        message = SimpleNamespace(edit=AsyncMock())
        user = SimpleNamespace(id=7, send=AsyncMock(return_value=message))
        profile = {"profile_id": "profile-7"}
        cog = SimpleNamespace(
            config=SimpleNamespace(
                approval_base_url=_Value("https://wallet.example/cryptowallet"),
                delegation_duration_days=_Value(30),
                delegation_max_duration_days=_Value(90),
                operating_mode=_Value("mainnet"),
                user_from_id=lambda user_id: SimpleNamespace(security_locked=_Value(False)),
            ),
            has_current_cryptowallet_mainnet_terms=AsyncMock(return_value=False),
            create_authorization_handoff=AsyncMock(return_value=(token, 1_800_000_000)),
            ensure_mainnet_wallet_profile=AsyncMock(return_value=profile),
        )

        expires_at = await WalletAuthorizationCommands.send_authorization_link(
            cog, user, profile
        )

        self.assertEqual(expires_at, 1_800_000_000)
        call = cog.create_authorization_handoff.await_args
        self.assertEqual(call.kwargs["terms"]["product"], "cryptowallet")
        self.assertEqual(call.kwargs["terms"]["version"], "2026-09-30.1")
        sent = user.send.await_args.kwargs
        self.assertEqual(sent["embed"].title, "Set Up Mainnet Wallet")
        self.assertIn("does not send funds", sent["embed"].fields[0].value)
        self.assertEqual(
            [item.label for item in sent["view"].children],
            ["Set Up Mainnet Wallet", "Confirm setup"],
        )

    async def test_testnet_keeps_existing_authorization_card(self):
        user = SimpleNamespace(id=7, send=AsyncMock())
        cog = SimpleNamespace(
            config=SimpleNamespace(
                approval_base_url=_Value("https://wallet.example/cryptowallet"),
                delegation_duration_days=_Value(30),
                delegation_max_duration_days=_Value(90),
                operating_mode=_Value("testnet"),
                user_from_id=lambda user_id: SimpleNamespace(security_locked=_Value(False)),
            ),
            create_authorization_handoff=AsyncMock(return_value=("token", 1_800_000_000)),
        )

        await WalletAuthorizationCommands.send_authorization_link(cog, user, {})

        cog.create_authorization_handoff.assert_awaited_once_with(
            7, {}, delegation_days=30
        )
        self.assertNotIn("view", user.send.await_args.kwargs)
        self.assertEqual(user.send.await_args.kwargs["embed"].title, "Authorize Crypto Wallet")


class MainnetSetupConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_records_terms_and_requires_active_authorization(self):
        cog = SimpleNamespace(
            has_current_cryptowallet_mainnet_terms=AsyncMock(return_value=False),
            poll_wallet_terms_result=AsyncMock(return_value={
                "product": "cryptowallet", "version": "2026-09-30.1",
                "acceptance_id": "acceptance-id",
            }),
            accept_cryptowallet_mainnet_terms=AsyncMock(),
            wallet_provider=SimpleNamespace(
                get_delegation_status=AsyncMock(return_value={"active": True})
            ),
        )
        view = WalletMainnetSetupView(
            cog, 7, {"profile_id": "profile-7"}, "https://wallet.example/setup",
            "r" * 32, int(time.time()) + 120,
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.children[1].callback(interaction)

        cog.accept_cryptowallet_mainnet_terms.assert_awaited_once_with(
            7, acceptance_id="acceptance-id"
        )
        cog.wallet_provider.get_delegation_status.assert_awaited_once_with(
            view.profile, "base-mainnet"
        )
        self.assertIn("mainnet wallet setup is ready", interaction.followup.send.await_args.args[0])
        self.assertTrue(all(item.disabled for item in view.children))

    async def test_confirm_does_not_repeat_current_terms(self):
        cog = SimpleNamespace(
            has_current_cryptowallet_mainnet_terms=AsyncMock(return_value=True),
            poll_wallet_terms_result=AsyncMock(),
            accept_cryptowallet_mainnet_terms=AsyncMock(),
            wallet_provider=SimpleNamespace(
                get_delegation_status=AsyncMock(return_value={"active": True})
            ),
        )
        view = WalletMainnetSetupView(
            cog, 7, {}, "https://wallet.example/setup", "r" * 32,
            int(time.time()) + 120,
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.children[1].callback(interaction)

        cog.poll_wallet_terms_result.assert_not_awaited()
        cog.accept_cryptowallet_mainnet_terms.assert_not_awaited()

class MainnetSetupHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_handoff_binds_exact_terms_product_version_and_handle(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import ec
        from ..backend.auth import _key_id
        from .test_authorization import _JwtHarness, _profile

        key = ec.generate_private_key(ec.SECP256R1())
        configuration = {
            "issuer": "https://wallet.example.test", "audience": "project-id",
            "kid": _key_id(key), "private_key": key,
        }
        harness = _JwtHarness(configuration)
        terms = {
            "product": "cryptowallet", "version": "2026-09-30.1",
            "result_handle": "r" * 32,
        }
        token, _ = await harness.create_authorization_handoff(
            7, _profile(), delegation_days=30, terms=terms
        )
        claims = jwt.decode(
            token, key.public_key(),
            algorithms=["ES256"], audience="project-id",
            issuer="https://wallet.example.test",
        )
        self.assertEqual(claims["sickwallet_terms"], terms)

    async def test_handoff_rejects_cross_product_or_extra_terms_fields(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from ..backend.auth import _key_id
        from .test_authorization import _JwtHarness, _profile

        key = ec.generate_private_key(ec.SECP256R1())
        harness = _JwtHarness({
            "issuer": "https://wallet.example.test", "audience": "project-id",
            "kid": _key_id(key), "private_key": key,
        })
        with self.assertRaisesRegex(ValueError, "terms setup binding"):
            await harness.create_authorization_handoff(
                7, _profile(), terms={
                    "product": "tokenfactory", "version": "2026-09-30.1",
                    "result_handle": "r" * 32,
                }
            )
        with self.assertRaisesRegex(ValueError, "terms setup binding"):
            await harness.create_authorization_handoff(
                7, _profile(), terms={
                    "product": "cryptowallet", "version": "2026-09-30.1",
                    "result_handle": "r" * 32, "secret": "forbidden",
                }
            )


class MainnetSetupWebTests(unittest.TestCase):
    def test_session_page_has_one_guided_terms_and_authorization_action(self):
        from pathlib import Path

        web = Path(__file__).resolve().parents[1] / "web"
        page = (web / "session.html").read_text(encoding="utf-8")
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn("View CryptoWallet terms", page)
        self.assertIn("I have read and accept", page)
        self.assertIn("Continue mainnet setup", script)
        self.assertIn("Return to Discord and press Confirm setup", script)
        self.assertIn("session.terms) await submitTerms", script)
        self.assertIn("authorizeWallet", script)

class MainnetSetupExistingAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_authorization_requests_only_missing_mainnet_terms(self):
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7), send=AsyncMock(), clean_prefix="!"
        )
        cog = SimpleNamespace(
            _wallet_read_allowed=AsyncMock(return_value=True),
            config=SimpleNamespace(operating_mode=_Value("mainnet")),
            _wallet_profile_or_error=AsyncMock(return_value={"profile_id": "profile-7"}),
            _wallet_environment=AsyncMock(
                return_value=__import__(
                    "cryptowallet.core.environment", fromlist=["WalletEnvironment"]
                ).WalletEnvironment.MAINNET
            ),
            has_current_cryptowallet_mainnet_terms=AsyncMock(return_value=False),
            _send_wallet_terms_acceptance=AsyncMock(return_value=1_800_000_000),
            wallet_provider=SimpleNamespace(get_delegation_status=AsyncMock(return_value={
                "active": True, "expires_at": "2027-01-01T00:00:00+00:00",
                "scope": "accounts",
            })),
        )

        await WalletAuthorizationCommands.wallet_authorize.callback(cog, ctx)

        cog._send_wallet_terms_acceptance.assert_awaited_once_with(ctx.author)
        self.assertIn("authorization is already active", ctx.send.await_args.args[0])
        self.assertIn("remaining protected mainnet terms step", ctx.send.await_args.args[0])
