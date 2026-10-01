import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..clanker import Clanker
from ..terms import (
    CLANKER_MAINNET_TERMS_VERSION,
    create_clanker_terms_acceptance,
    is_current_clanker_terms_acceptance,
)
from ..views import ClankerTermsView


class ClankerTermsRecordTests(unittest.TestCase):
    def test_record_is_exact_product_user_and_version(self):
        record = create_clanker_terms_acceptance(
            42, now=1000, acceptance_id="clanker-acceptance"
        )
        self.assertEqual(record["product"], "clanker")
        self.assertEqual(record["version"], CLANKER_MAINNET_TERMS_VERSION)
        self.assertTrue(is_current_clanker_terms_acceptance(record, 42))
        self.assertFalse(is_current_clanker_terms_acceptance(record, 43))
        self.assertFalse(is_current_clanker_terms_acceptance(
            {**record, "product": "cryptowallet"}, 42
        ))
        self.assertFalse(is_current_clanker_terms_acceptance(
            {**record, "product": "tokenfactory"}, 42
        ))
        self.assertFalse(is_current_clanker_terms_acceptance(
            {**record, "version": "old"}, 42
        ))

    def test_record_rejects_invalid_or_secret_bearing_shapes(self):
        with self.assertRaises(ValueError):
            create_clanker_terms_acceptance(0, now=1000, acceptance_id="id")
        record = create_clanker_terms_acceptance(
            42, now=1000, acceptance_id="clanker-acceptance"
        )
        self.assertFalse(is_current_clanker_terms_acceptance(
            {**record, "wallet_secret": "forbidden"}, 42
        ))


class ClankerTermsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_has_view_and_green_accept_controls(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=42), send=AsyncMock())
        wallet = SimpleNamespace(config=SimpleNamespace(
            approval_base_url=AsyncMock(return_value="https://wallet.example/")
        ))
        cog = SimpleNamespace(
            bot=SimpleNamespace(get_cog=lambda name: wallet if name == "CryptoWallet" else None),
            has_current_mainnet_terms=AsyncMock(return_value=False),
        )
        await Clanker.clanker_terms.callback(cog, ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        view = ctx.send.await_args.kwargs["view"]
        self.assertIn("Acceptance: **Not accepted**", embed.description)
        self.assertIn("Base Sepolia test launches are unchanged", embed.description)
        self.assertEqual([item.label for item in view.children], ["View terms", "Accept terms"])
        self.assertEqual(view.children[1].style.name, "success")

    async def test_green_button_records_acceptance_directly_in_discord(self):
        cog = SimpleNamespace(
            has_current_mainnet_terms=AsyncMock(return_value=False),
            accept_mainnet_terms=AsyncMock(return_value={"product": "clanker"}),
        )
        view = ClankerTermsView(cog, 42, None, current=False)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(defer=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await view.children[0].callback(interaction)
        cog.accept_mainnet_terms.assert_awaited_once_with(42)
        self.assertEqual(view.children[0].label, "Terms accepted")
        self.assertTrue(view.children[0].disabled)

    async def test_current_acceptance_is_disabled_and_never_unaccepted(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=42), send=AsyncMock())
        cog = SimpleNamespace(
            bot=SimpleNamespace(get_cog=lambda name: None),
            has_current_mainnet_terms=AsyncMock(return_value=True),
        )
        await Clanker.clanker_terms.callback(cog, ctx)
        view = ctx.send.await_args.kwargs["view"]
        self.assertEqual([item.label for item in view.children], ["Terms accepted"])
        self.assertTrue(view.children[0].disabled)

    def test_page_is_clanker_specific_and_retainable(self):
        page = (
            Path(__file__).resolve().parents[2]
            / "cryptowallet" / "web" / "clanker-terms.html"
        ).read_text(encoding="utf-8")
        self.assertIn("do not accept CryptoWallet or TokenFactory terms", page)
        self.assertIn("immutable launch fingerprint", page)
        self.assertIn("SickGaming facilitates submission and receives only", page)
        self.assertIn("Reading this page does not accept", page)
