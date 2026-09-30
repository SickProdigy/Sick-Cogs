import unittest
from unittest.mock import AsyncMock

from ..backend.config import WalletConfigMixin
from ..backend.terms import (
    CRYPTOWALLET_MAINNET_TERMS_VERSION,
    create_cryptowallet_terms_acceptance,
    is_current_cryptowallet_terms_acceptance,
)


class CryptoWalletTermsRecordTests(unittest.TestCase):
    def test_record_is_product_user_and_version_bound(self):
        record = create_cryptowallet_terms_acceptance(
            42, now=1000, acceptance_id="acceptance-1"
        )
        self.assertEqual(record["product"], "cryptowallet")
        self.assertEqual(record["version"], CRYPTOWALLET_MAINNET_TERMS_VERSION)
        self.assertEqual(record["discord_user_id"], 42)
        self.assertEqual(record["accepted_at"], 1000)
        self.assertEqual(record["acceptance_id"], "acceptance-1")
        self.assertTrue(is_current_cryptowallet_terms_acceptance(record, 42))

    def test_other_user_or_product_never_inherits_acceptance(self):
        record = create_cryptowallet_terms_acceptance(
            42, now=1000, acceptance_id="acceptance-1"
        )
        self.assertFalse(is_current_cryptowallet_terms_acceptance(record, 43))
        for product in ("tokenfactory", "clanker"):
            changed = {**record, "product": product}
            self.assertFalse(is_current_cryptowallet_terms_acceptance(changed, 42))

    def test_stale_version_and_extra_fields_fail_closed(self):
        record = create_cryptowallet_terms_acceptance(
            42, now=1000, acceptance_id="acceptance-1"
        )
        self.assertFalse(is_current_cryptowallet_terms_acceptance(
            {**record, "version": "older"}, 42
        ))
        self.assertFalse(is_current_cryptowallet_terms_acceptance(
            {**record, "secret": "must-not-be-stored"}, 42
        ))

    def test_invalid_identifiers_are_rejected(self):
        for user_id, timestamp, identifier in (
            (0, 1000, "id"), (42, 0, "id"), (42, 1000, ""),
        ):
            with self.assertRaises(ValueError):
                create_cryptowallet_terms_acceptance(
                    user_id, now=timestamp, acceptance_id=identifier
                )


class _AcceptanceValue:
    def __init__(self, value=None):
        self.value = value
        self.set = AsyncMock(side_effect=self._set)

    async def _set(self, value):
        self.value = value

    async def __call__(self):
        return self.value


class _UserConfig:
    def __init__(self):
        self.mainnet_terms_acceptance = _AcceptanceValue()


class _Config:
    def __init__(self):
        self.users = {}

    def user_from_id(self, user_id):
        return self.users.setdefault(int(user_id), _UserConfig())


class CryptoWalletTermsStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_is_per_user_and_current_version_only(self):
        wallet = type("Wallet", (WalletConfigMixin,), {})()
        wallet.config = _Config()
        record = await wallet.accept_cryptowallet_mainnet_terms(
            42, acceptance_id="acceptance-1", now=1000
        )
        self.assertEqual(record, wallet.config.users[42].mainnet_terms_acceptance.value)
        self.assertTrue(await wallet.has_current_cryptowallet_mainnet_terms(42))
        self.assertFalse(await wallet.has_current_cryptowallet_mainnet_terms(43))
