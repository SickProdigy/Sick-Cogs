import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..terms import (
    TOKENFACTORY_MAINNET_TERMS_VERSION,
    create_tokenfactory_terms_acceptance,
    is_current_tokenfactory_terms_acceptance,
)
from ..tokenfactory import TokenFactory


class TokenFactoryTermsRecordTests(unittest.TestCase):
    def test_record_is_exact_product_user_and_version(self):
        record = create_tokenfactory_terms_acceptance(
            42, now=1000, acceptance_id="tf-acceptance"
        )
        self.assertEqual(record["product"], "tokenfactory")
        self.assertEqual(record["version"], TOKENFACTORY_MAINNET_TERMS_VERSION)
        self.assertTrue(is_current_tokenfactory_terms_acceptance(record, 42))
        self.assertFalse(is_current_tokenfactory_terms_acceptance(record, 43))
        self.assertFalse(is_current_tokenfactory_terms_acceptance(
            {**record, "product": "cryptowallet"}, 42
        ))
        self.assertFalse(is_current_tokenfactory_terms_acceptance(
            {**record, "version": "old"}, 42
        ))

    def test_record_rejects_invalid_or_secret_bearing_shapes(self):
        with self.assertRaises(ValueError):
            create_tokenfactory_terms_acceptance(0, now=1000, acceptance_id="id")
        record = create_tokenfactory_terms_acceptance(
            42, now=1000, acceptance_id="tf-acceptance"
        )
        self.assertFalse(is_current_tokenfactory_terms_acceptance(
            {**record, "wallet_secret": "forbidden"}, 42
        ))


class TokenFactoryTermsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_is_reference_only_and_product_specific(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=42), send=AsyncMock())
        wallet = SimpleNamespace(config=SimpleNamespace(
            approval_base_url=AsyncMock(return_value="https://wallet.example/")
        ))
        cog = SimpleNamespace(
            bot=SimpleNamespace(get_cog=lambda name: wallet if name == "CryptoWallet" else None),
            has_current_mainnet_terms=AsyncMock(return_value=False),
        )
        await TokenFactory.tokenfactory_terms.callback(cog, ctx)
        message = ctx.send.await_args.args[0]
        self.assertIn("TokenFactory mainnet terms", message)
        self.assertIn("tokenfactory-terms.html", message)
        self.assertIn("Base Sepolia", message)
        self.assertIn("does not accept", message)

    def test_page_excludes_other_product_acceptance(self):
        from pathlib import Path
        page = (Path(__file__).resolve().parents[2] / "cryptowallet" / "web" / "tokenfactory-terms.html").read_text(encoding="utf-8")
        self.assertIn("do not accept CryptoWallet or Clanker terms", page)
        self.assertIn("charges no TokenFactory service fee", page)
        self.assertIn("does not hold the created token supply", page)
        self.assertIn("Reading this page does not accept", page)
