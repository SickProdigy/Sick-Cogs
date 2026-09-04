import base64
import copy
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
from jwt import DecodeError, ExpiredSignatureError, InvalidAudienceError
from cryptography.hazmat.primitives.asymmetric import ec

from ..backend.auth import CLAIM_HANDOFF_LIFETIME_SECONDS, JwtAuthMixin, _key_id
from ..backend.sessions import ApprovalSessionMixin
from ..commands.account import WalletAccountCommands
from ..commands.activity import WalletActivityCommands
from ..commands.authorization import WalletAuthorizationCommands
from ..commands.views import WalletAuthorizationView, WalletRevocationView
from ..commands.transactions import WalletTransactionCommands
from ..commands.core import WalletCoreCommands
from ..commands.admin import WalletAdminCommands
from ..core.models import (
    ApprovalPurpose, ApprovalStatus, IntentStatus, TransactionIntent
)
from ..core.networks import (
    AVALANCHE_FUJI,
    ARBITRUM_SEPOLIA,
    BASE_SEPOLIA,
    ETHEREUM_SEPOLIA,
    POLYGON_AMOY,
    SOLANA_DEVNET,
    KNOWN_NETWORKS,
    NETWORKS,
    ChainFamily,
    Network,
    NetworkCapabilities,
    NetworkCapability,
)
from ..core.validation import (
    format_atomic_amount,
    normalize_address_for_network,
    normalize_solana_address,
    normalize_solana_signature,
    parse_native_amount,
)
from ..providers.cdp import CdpWalletProvider
from ..providers.base_rpc import (
    _decode_abi_text,
    build_solana_transfer_message,
    serialize_unsigned_solana_transfer,
)


class _Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value


class _MutableValue(_Value):
    async def set(self, value):
        self.value = value


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


class _ApprovalStore:
    def __init__(self):
        self.data = {}

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.data

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _SessionConfig:
    def __init__(self, deployment_id="deployment"):
        self.deployment_id = _Value(deployment_id)
        self.stores = {}

    def user_from_id(self, user_id):
        store = self.stores.setdefault(int(user_id), _ApprovalStore())
        return SimpleNamespace(approval_sessions=store)

    async def all_users(self):
        return {
            user_id: {"approval_sessions": store.data}
            for user_id, store in self.stores.items()
        }


class _SessionHarness(ApprovalSessionMixin):
    def __init__(self, deployment_id="deployment", application_id=42):
        self.config = _SessionConfig(deployment_id)
        self.application_id = application_id

    def discord_application_id(self):
        return self.application_id


class AuthorizationViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_handoff_is_sent_as_message_content(self):
        token = "x" * 600
        user = SimpleNamespace(id=7, send=AsyncMock())
        cog = SimpleNamespace(
            config=SimpleNamespace(
                approval_base_url=_Value("https://wallet.example.test/cryptowallet"),
                user_from_id=lambda user_id: SimpleNamespace(
                    security_locked=_Value(False)
                ),
            ),
            create_authorization_handoff=AsyncMock(
                return_value=(token, 1_800_000_000)
            ),
        )
        expires_at = await WalletAuthorizationCommands.send_authorization_link(
            cog, user, _profile()
        )
        sent = user.send.await_args.kwargs
        self.assertEqual(expires_at, 1_800_000_000)
        self.assertEqual(
            sent["content"],
            f"🔐 [Open protected authorization page](https://wallet.example.test/cryptowallet/session.html#handoff={token})",
        )
        self.assertNotIn("view", sent)

    async def test_recovery_handoff_is_sent_as_message_content(self):
        token = "x" * 600
        author = SimpleNamespace(id=7, send=AsyncMock())
        ctx = SimpleNamespace(author=author, send=AsyncMock())
        cog = SimpleNamespace(
            _wallet_read_allowed=AsyncMock(return_value=True),
            _wallet_sensitive_allowed=AsyncMock(return_value=True),
            _wallet_profile_or_error=AsyncMock(return_value=_profile()),
            _account_for_network=lambda profile, network: profile["accounts"][0],
            config=SimpleNamespace(
                approval_base_url=_Value("https://wallet.example.test/cryptowallet")
            ),
            create_recovery_handoff=AsyncMock(
                return_value=(token, 1_800_000_000)
            ),
        )
        await WalletAccountCommands.wallet_recovery.callback(cog, ctx)
        sent = author.send.await_args.kwargs
        self.assertEqual(
            sent["content"],
            f"🛟 [Open protected recovery page](https://wallet.example.test/cryptowallet/recovery.html#handoff={token})",
        )
        self.assertNotIn("view", sent)
        self.assertIn("protected wallet recovery link", ctx.send.await_args.args[0])

    async def test_emergency_lock_blocks_new_authorization_link(self):
        locked_config = SimpleNamespace(security_locked=_Value(True))
        cog = SimpleNamespace(
            config=SimpleNamespace(user_from_id=lambda user_id: locked_config)
        )
        with self.assertRaisesRegex(RuntimeError, "emergency-locked"):
            await WalletAuthorizationCommands.send_authorization_link(
                cog, SimpleNamespace(id=7), _profile()
            )

    async def test_emergency_lock_blocks_sensitive_commands_but_explains_reads(self):
        locked_config = SimpleNamespace(security_locked=_Value(True))
        ctx = SimpleNamespace(author=SimpleNamespace(id=7), send=AsyncMock())
        cog = SimpleNamespace(
            config=SimpleNamespace(user=lambda user: locked_config)
        )
        allowed = await WalletCoreCommands._wallet_sensitive_allowed(cog, ctx)
        self.assertFalse(allowed)
        message = ctx.send.await_args.args[0]
        self.assertIn("Receiving funds", message)
        self.assertIn("authorization revocation remain available", message)

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
        profile = _profile()
        profile["accounts"].append({
            "network": "solana-devnet",
            "address": "HpabPRRCFbBKSuJr5PdkVvQc85FyxyTWkFM2obBRSvHT",
        })
        before = int(time.time())
        token, expires_at = await harness.create_authorization_handoff(7, profile)
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
        self.assertEqual(claims["sickwallet_accounts"], [
            {"family": "evm", "address": "0x7930fB6E9853B3835Cf047f36855993cb82d4387"},
            {"family": "solana", "address": "HpabPRRCFbBKSuJr5PdkVvQc85FyxyTWkFM2obBRSvHT"},
        ])
        self.assertEqual(
            claims["sickwallet_address"],
            "0x7930fB6E9853B3835Cf047f36855993cb82d4387",
        )
        self.assertGreaterEqual(expires_at, before + CLAIM_HANDOFF_LIFETIME_SECONDS)
        self.assertLessEqual(expires_at, int(time.time()) + CLAIM_HANDOFF_LIFETIME_SECONDS)

    async def test_recovery_handoff_has_a_distinct_bound_purpose(self):
        harness = _JwtHarness(self.configuration)
        token, _ = await harness.create_recovery_handoff(7, _profile())
        claims = jwt.decode(
            token,
            self.key.public_key(),
            algorithms=["ES256"],
            audience="project-id",
            issuer="https://wallet.example.test",
        )
        self.assertEqual(claims["sickwallet_purpose"], "recovery")
        self.assertEqual(claims["sub"], "profile-7")
        self.assertEqual(claims["sickwallet_discord_user"], "7")
        self.assertEqual(
            claims["sickwallet_address"],
            "0x7930fB6E9853B3835Cf047f36855993cb82d4387",
        )

    async def test_malformed_handoff_is_rejected_by_jwt_decoder(self):
        with self.assertRaises(DecodeError):
            jwt.decode(
                "not-a-jwt",
                self.key.public_key(),
                algorithms=["ES256"],
                audience="project-id",
                issuer="https://wallet.example.test",
            )

    async def test_expired_handoff_is_rejected_by_jwt_decoder(self):
        harness = _JwtHarness(self.configuration)
        token, _ = await harness.create_authorization_handoff(7, _profile())
        claims = jwt.decode(token, options={"verify_signature": False})
        claims["exp"] = int(time.time()) - 1
        expired = jwt.encode(
            claims, self.key, algorithm="ES256", headers={"kid": self.configuration["kid"]}
        )
        with self.assertRaises(ExpiredSignatureError):
            jwt.decode(
                expired,
                self.key.public_key(),
                algorithms=["ES256"],
                audience="project-id",
                issuer="https://wallet.example.test",
            )

    async def test_wrong_project_handoff_is_rejected_by_jwt_decoder(self):
        harness = _JwtHarness(self.configuration)
        token, _ = await harness.create_authorization_handoff(7, _profile())
        with self.assertRaises(InvalidAudienceError):
            jwt.decode(
                token,
                self.key.public_key(),
                algorithms=["ES256"],
                audience="other-project",
                issuer="https://wallet.example.test",
            )

    async def test_unsupported_handoff_purpose_is_rejected(self):
        harness = _JwtHarness(self.configuration)
        with self.assertRaises(ValueError):
            await harness._create_wallet_handoff(
                7, _profile(), purpose="transaction"
            )

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
        invalid_account = copy.deepcopy(_profile())
        invalid_account["accounts"][0]["address"] = "not-an-address"
        cases.append(invalid_account)
        harness = _JwtHarness(self.configuration)
        for profile in cases:
            with self.subTest(profile=profile):
                with self.assertRaises(RuntimeError):
                    await harness.create_authorization_handoff(7, profile)


class SecurityLockCommandTests(unittest.IsolatedAsyncioTestCase):

    def _user_config(self, *, locked=False):
        intents = _ApprovalStore()
        intents.data["pending-intent"] = {"status": "pending"}
        return SimpleNamespace(
            security_locked=_MutableValue(locked),
            security_locked_at=_MutableValue(0),
            security_lock_source=_MutableValue(None),
            intents=intents,
            profile=_Value(None),
        )

    async def test_user_lock_persists_before_provider_and_rejects_pending_intents(self):
        user_config = self._user_config()
        author = SimpleNamespace(id=7)
        ctx = SimpleNamespace(author=author, send=AsyncMock())
        cog = SimpleNamespace(
            config=SimpleNamespace(user=lambda user: user_config),
            wallet_provider=SimpleNamespace(revoke_authorization=AsyncMock()),
        )
        await WalletCoreCommands.wallet_security_lock.callback(cog, ctx)
        self.assertTrue(user_config.security_locked.value)
        self.assertGreater(user_config.security_locked_at.value, 0)
        self.assertEqual(user_config.security_lock_source.value, "user")
        self.assertEqual(user_config.intents.data["pending-intent"]["status"], "rejected")
        cog.wallet_provider.revoke_authorization.assert_not_awaited()

    def test_owner_target_parser_accepts_mentions_and_raw_ids(self):
        parser = lambda value: WalletAdminCommands._wallet_user_id(None, value)
        self.assertEqual(parser("123456789012345678"), 123456789012345678)
        self.assertEqual(parser(f"<{chr(64)}123456789012345678>"), 123456789012345678)
        self.assertEqual(parser(f"<{chr(64)}!123456789012345678>"), 123456789012345678)
        self.assertIsNone(parser("username"))
        self.assertIsNone(parser("0"))

    async def test_only_owner_command_removes_lock_without_authorizing(self):
        user_config = self._user_config(locked=True)
        ctx = SimpleNamespace(send=AsyncMock())
        cog = SimpleNamespace(
            config=SimpleNamespace(user_from_id=lambda user_id: user_config),
            _wallet_user_id=lambda reference: WalletAdminCommands._wallet_user_id(
                None, reference
            ),
        )
        await WalletAdminCommands.walletset_unlock.callback(cog, ctx, "7")
        self.assertFalse(user_config.security_locked.value)
        self.assertEqual(user_config.security_locked_at.value, 0)
        self.assertIsNone(user_config.security_lock_source.value)
        self.assertIn("No signing authorization was created", ctx.send.await_args.args[0])


class StoredApprovalSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_or_unknown_session_token_is_rejected(self):
        harness = _SessionHarness()
        self.assertIsNone(await harness.resolve_approval_session("short"))
        self.assertIsNone(await harness.resolve_approval_session("x" * 40))

    async def test_expired_session_is_rejected(self):
        harness = _SessionHarness()
        token = await harness.create_approval_session(7, ApprovalPurpose.RECOVERY)
        digest = harness._token_digest(token)
        harness.config.stores[7].data[digest]["expires_at"] = int(time.time()) - 1
        self.assertIsNone(await harness.resolve_approval_session(token))

    async def test_wrong_discord_user_cannot_consume_session(self):
        harness = _SessionHarness()
        token = await harness.create_approval_session(7, ApprovalPurpose.RECOVERY)
        self.assertIsNone(await harness.establish_browser_session(token, 8))
        self.assertIsNotNone(await harness.resolve_approval_session(token))

    async def test_consumed_session_rejects_replay(self):
        harness = _SessionHarness()
        token = await harness.create_approval_session(7, ApprovalPurpose.RECOVERY)
        browser_token = await harness.establish_browser_session(token, 7)
        self.assertIsNotNone(browser_token)
        self.assertIsNone(await harness.resolve_approval_session(token))
        self.assertIsNone(await harness.establish_browser_session(token, 7))
        resolved = await harness.resolve_browser_session(browser_token)
        self.assertIsNotNone(resolved)
        self.assertIs(resolved.status, ApprovalStatus.IDENTITY_VERIFIED)
        self.assertIs(resolved.purpose, ApprovalPurpose.RECOVERY)

    async def test_wrong_deployment_or_application_rejects_session(self):
        harness = _SessionHarness()
        token = await harness.create_approval_session(7, ApprovalPurpose.SECURITY)
        harness.config.deployment_id = _Value("foreign-deployment")
        self.assertIsNone(await harness.resolve_approval_session(token))

        harness = _SessionHarness()
        token = await harness.create_approval_session(7, ApprovalPurpose.SECURITY)
        harness.application_id = 99
        self.assertIsNone(await harness.resolve_approval_session(token))


class FailClosedTransactionTests(unittest.TestCase):
    def test_uncertain_intent_round_trips_and_warns_against_replacement(self):
        intent = TransactionIntent(
            intent_id="intent-7",
            profile_id="profile-7",
            network="base-sepolia",
            from_address="0x7930fB6E9853B3835Cf047f36855993cb82d4387",
            to_address="0x7930fB6E9853B3835Cf047f36855993cb82d4387",
            value_wei=1,
            created_at=1_800_000_000,
            expires_at=1_800_000_900,
            gas_sponsored=True,
            status=IntentStatus.UNCERTAIN,
            provider_status="unknown",
        )
        restored = TransactionIntent.from_dict(intent.to_dict())
        self.assertIs(restored.status, IntentStatus.UNCERTAIN)
        embed = WalletTransactionCommands._intent_embed(
            restored, NETWORKS[restored.network], None
        )
        self.assertEqual(embed.title, "Transaction outcome uncertain")
        self.assertIn("do not send a replacement", embed.footer.text)


class NetworkArchitectureTests(unittest.TestCase):
    def test_base_capabilities_are_explicit_and_provider_declared(self):
        self.assertEqual(
            set(NETWORKS),
            {
                BASE_SEPOLIA.key,
                ETHEREUM_SEPOLIA.key,
                ARBITRUM_SEPOLIA.key,
                POLYGON_AMOY.key,
                AVALANCHE_FUJI.key,
                SOLANA_DEVNET.key,
            },
        )
        self.assertEqual(
            set(KNOWN_NETWORKS), set(NETWORKS)
        )
        self.assertTrue(ETHEREUM_SEPOLIA.enabled)
        self.assertEqual(ETHEREUM_SEPOLIA.chain_id, 11155111)
        self.assertEqual(
            ETHEREUM_SEPOLIA.capabilities.enabled(), (
                NetworkCapability.BALANCE,
                NetworkCapability.TOKEN_DISCOVERY,
                NetworkCapability.HISTORY,
                NetworkCapability.TRANSACTION_LOOKUP,
            )
        )
        self.assertIs(BASE_SEPOLIA.family, ChainFamily.EVM)
        self.assertEqual(BASE_SEPOLIA.reference_label, "chain ID")
        self.assertEqual(BASE_SEPOLIA.reference, "84532")
        self.assertTrue(BASE_SEPOLIA.supports(NetworkCapability.SEND))
        provider = CdpWalletProvider(SimpleNamespace())
        self.assertTrue(provider.supports(BASE_SEPOLIA.key, NetworkCapability.SEND))
        self.assertTrue(
            provider.supports(ETHEREUM_SEPOLIA.key, NetworkCapability.BALANCE)
        )
        self.assertTrue(
            provider.supports(ETHEREUM_SEPOLIA.key, NetworkCapability.HISTORY)
        )
        self.assertFalse(
            provider.supports(ETHEREUM_SEPOLIA.key, NetworkCapability.SEND)
        )
        self.assertFalse(ETHEREUM_SEPOLIA.supports(NetworkCapability.DELEGATION))
        profile = _profile()
        self.assertIs(
            WalletCoreCommands._account_for_network(
                profile, ETHEREUM_SEPOLIA.key
            ),
            profile["accounts"][0],
        )

    def test_activity_network_aliases_are_explicit(self):
        self.assertIs(WalletActivityCommands._activity_network("base"), BASE_SEPOLIA)
        self.assertIs(
            WalletActivityCommands._activity_network("eth"), ETHEREUM_SEPOLIA
        )
        self.assertIs(WalletActivityCommands._activity_network("arb"), ARBITRUM_SEPOLIA)
        self.assertIs(WalletActivityCommands._activity_network("polygon"), POLYGON_AMOY)
        self.assertIs(WalletActivityCommands._activity_network("avax"), AVALANCHE_FUJI)
        self.assertIs(WalletActivityCommands._activity_network("sol"), SOLANA_DEVNET)
        self.assertIsNone(WalletActivityCommands._activity_network("unknown"))

    def test_additional_evm_testnets_remain_read_only(self):
        provider = CdpWalletProvider(SimpleNamespace())
        expected = {
            ARBITRUM_SEPOLIA: (421614, "ETH"),
            POLYGON_AMOY: (80002, "POL"),
            AVALANCHE_FUJI: (43113, "AVAX"),
        }
        for network, (chain_id, symbol) in expected.items():
            with self.subTest(network=network.key):
                self.assertEqual(network.chain_id, chain_id)
                self.assertEqual(network.native_symbol, symbol)
                self.assertTrue(network.supports(NetworkCapability.BALANCE))
                self.assertTrue(network.supports(NetworkCapability.TRANSACTION_LOOKUP))
                self.assertFalse(network.supports(NetworkCapability.HISTORY))
                self.assertFalse(network.supports(NetworkCapability.SEND))
                self.assertFalse(network.supports(NetworkCapability.TOKEN_DISCOVERY))
                self.assertTrue(provider.supports(network.key, NetworkCapability.BALANCE))
                self.assertFalse(provider.supports(network.key, NetworkCapability.SEND))

    def test_activity_embed_uses_selected_network_explorer(self):
        address = _profile()["accounts"][0]["address"]
        tx_hash = "0x" + "a" * 64
        page = {
            "transactions": [{
                "transaction_hash": tx_hash,
                "content": {
                    "from": address,
                    "to": "0x1111111111111111111111111111111111111111",
                    "value": "0x1",
                },
            }],
            "has_more": False,
            "next_page": "",
        }
        embed = WalletActivityCommands._activity_embed(
            address, page, 0, 0, ETHEREUM_SEPOLIA
        )
        self.assertIn(ETHEREUM_SEPOLIA.explorer_url, embed.fields[0].value)
        self.assertNotIn(BASE_SEPOLIA.explorer_url, embed.fields[0].value)

    def test_disabled_solana_metadata_cannot_enable_capabilities(self):
        solana = Network(
            key="solana-devnet",
            name="Solana Devnet",
            family=ChainFamily.SOLANA,
            cluster="devnet",
            native_symbol="SOL",
            native_decimals=9,
            explorer_url="https://explorer.solana.com",
            testnet=True,
            enabled=False,
            capabilities=NetworkCapabilities(balance=True),
        )
        self.assertEqual(solana.reference_label, "cluster")
        self.assertEqual(solana.reference, "devnet")
        self.assertFalse(solana.supports(NetworkCapability.BALANCE))
        with self.assertRaisesRegex(ValueError, "not enabled"):
            normalize_address_for_network(
                "0x7930fB6E9853B3835Cf047f36855993cb82d4387", solana
            )
        self.assertEqual(parse_native_amount("1.25", solana), 1_250_000_000)
        self.assertEqual(format_atomic_amount(1_250_000_000, solana), "1.25")

    def test_solana_devnet_is_read_only_and_uses_cluster_links(self):
        address = "HpabPRRCFbBKSuJr5PdkVvQc85FyxyTWkFM2obBRSvHT"
        provider = CdpWalletProvider(SimpleNamespace())
        self.assertEqual(normalize_solana_address(address), address)
        self.assertIs(SOLANA_DEVNET.family, ChainFamily.SOLANA)
        self.assertEqual(SOLANA_DEVNET.reference, "devnet")
        self.assertEqual(SOLANA_DEVNET.native_decimals, 9)
        self.assertTrue(SOLANA_DEVNET.supports(NetworkCapability.BALANCE))
        self.assertFalse(SOLANA_DEVNET.supports(NetworkCapability.SEND))
        self.assertTrue(SOLANA_DEVNET.supports(NetworkCapability.HISTORY))
        self.assertTrue(SOLANA_DEVNET.supports(NetworkCapability.TRANSACTION_LOOKUP))
        self.assertTrue(provider.supports(SOLANA_DEVNET.key, NetworkCapability.BALANCE))
        self.assertFalse(provider.supports(SOLANA_DEVNET.key, NetworkCapability.SEND))
        self.assertEqual(
            SOLANA_DEVNET.explorer_address_url(address),
            f"https://explorer.solana.com/address/{address}?cluster=devnet",
        )
        with self.assertRaises(ValueError):
            normalize_solana_address("0OIl" * 8)
        self.assertEqual(normalize_solana_signature("1" * 64), "1" * 64)
        with self.assertRaises(ValueError):
            normalize_solana_signature("0" * 88)

        page = {
            "transactions": [{
                "signature": "1" * 64,
                "block_time": 1_800_000_000,
                "success": True,
                "account_changes": [{
                    "address": address,
                    "change_atomic": 1_250_000_000,
                }],
            }]
        }
        embed = WalletActivityCommands._activity_embed(address, page, 0, 0, SOLANA_DEVNET)
        self.assertIn("Received", embed.fields[0].name)
        self.assertIn("1.25 SOL net", embed.fields[0].name)
        self.assertIn("cluster=devnet", embed.fields[0].value)

    def test_unsigned_solana_transfer_has_one_signer_and_system_instruction(self):
        sender = "HpabPRRCFbBKSuJr5PdkVvQc85FyxyTWkFM2obBRSvHT"
        recipient = "11111111111111111111111111111111"
        message = build_solana_transfer_message(sender, recipient, 1250, sender)
        self.assertEqual(message[:3], bytes((1, 0, 1)))
        self.assertEqual(message[3], 3)
        self.assertEqual(message[-18:-12], bytes((1, 2, 2, 0, 1, 12)))
        self.assertEqual(message[-12:], bytes.fromhex("02000000e204000000000000"))
        wire = base64.b64decode(serialize_unsigned_solana_transfer(message))
        self.assertEqual(wire[0], 1)
        self.assertEqual(wire[1:65], b"\x00" * 64)
        self.assertEqual(wire[65:], message)
        with self.assertRaises(ValueError):
            build_solana_transfer_message(sender, recipient, 0, sender)

    def test_cdp_profile_preserves_separate_evm_and_solana_accounts(self):
        evm_address = _profile()["accounts"][0]["address"]
        solana_address = "HpabPRRCFbBKSuJr5PdkVvQc85FyxyTWkFM2obBRSvHT"
        profile = CdpWalletProvider._profile_from_end_user(
            {
                "userId": "profile-7",
                "evmSmartAccountObjects": [{"address": evm_address}],
                "solanaAccountObjects": [{"address": solana_address}],
            },
            "profile-7",
            7,
        ).to_dict()
        self.assertEqual(
            [(item["network"], item["address"]) for item in profile["accounts"]],
            [
                (BASE_SEPOLIA.key, evm_address),
                (SOLANA_DEVNET.key, solana_address),
            ],
        )

    def test_intent_reads_legacy_wei_and_writes_neutral_atomic_amount(self):
        legacy = {
            "intent_id": "intent-atomic",
            "profile_id": "profile-atomic",
            "network": BASE_SEPOLIA.key,
            "from_address": "from",
            "to_address": "to",
            "value_wei": 7,
            "estimated_gas_fee_wei": 3,
            "created_at": 1,
            "expires_at": 2,
        }
        stored = TransactionIntent.from_dict(legacy).to_dict()
        self.assertEqual(stored["value_atomic"], 7)
        self.assertEqual(stored["estimated_fee_atomic"], 3)
        self.assertEqual(stored["value_wei"], 7)


class PortfolioBalanceTests(unittest.IsolatedAsyncioTestCase):
    def test_erc20_text_decoder_accepts_dynamic_and_bytes32_metadata(self):
        text = b"SGST"
        dynamic = (32).to_bytes(32, "big") + len(text).to_bytes(32, "big") + text.ljust(32, b"\x00")
        self.assertEqual(_decode_abi_text("0x" + dynamic.hex()), "SGST")
        self.assertEqual(_decode_abi_text("0x" + text.ljust(32, b"\x00").hex()), "SGST")

    async def test_token_discovery_excludes_native_and_preserves_contract(self):
        address = _profile()["accounts"][0]["address"]
        token_contract = "0x1111111111111111111111111111111111111111"
        client = SimpleNamespace(
            list_token_balances=AsyncMock(return_value={
                "balances": [
                    {
                        "token": {
                            "contractAddress": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                            "symbol": "ETH",
                        },
                        "amount": {"amount": "9", "decimals": 18},
                    },
                    {
                        "token": {"contractAddress": token_contract, "symbol": "TEST"},
                        "amount": {"amount": "1250000", "decimals": 6},
                    },
                ]
            })
        )
        provider = CdpWalletProvider(SimpleNamespace())
        provider.credentials = AsyncMock(return_value=object())
        provider._api_client = lambda credentials: client
        assets = await provider.get_token_balances(address, ETHEREUM_SEPOLIA.key)
        self.assertEqual(assets, [{
            "symbol": "TEST",
            "contract_address": token_contract,
            "amount_atomic": 1_250_000,
            "decimals": 6,
        }])
        self.assertEqual(format_atomic_amount(1_250_000, ETHEREUM_SEPOLIA, decimals=6), "1.25")


if __name__ == "__main__":
    unittest.main()
