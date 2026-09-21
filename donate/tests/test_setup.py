import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from donate.donate import DEFAULT_DESCRIPTION, DEFAULT_FOOTER, DEFAULT_TITLE, Donate
from donate.setup import (
    CardDetailsModal, MethodDeleteConfirmView, MethodModal, ResetConfirmView, owner_check,
)


class Value:
    def __init__(self, value):
        self.value = value
        self.set = AsyncMock(side_effect=self._set)

    async def _set(self, value):
        self.value = value

    async def __call__(self):
        if isinstance(self.value, dict):
            return {key: dict(item) for key, item in self.value.items()}
        if isinstance(self.value, list):
            return list(self.value)
        return self.value


class DonateSetupTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, *, methods=None, notes=None):
        group = SimpleNamespace(
            title=Value(DEFAULT_TITLE), description=Value(DEFAULT_DESCRIPTION),
            footer=Value(DEFAULT_FOOTER), methods=Value(methods or {}), notes=Value(notes or []),
            clear=AsyncMock(),
        )
        cog = object.__new__(Donate)
        cog.config = SimpleNamespace(guild=MagicMock(return_value=group))
        return cog, group

    def test_modal_prefills_existing_defaults_and_method_values(self):
        cog, _ = self.make_cog()
        guild = SimpleNamespace(id=1)
        details = CardDetailsModal(cog, guild, {
            "title": DEFAULT_TITLE, "description": DEFAULT_DESCRIPTION, "footer": DEFAULT_FOOTER,
        })
        method = MethodModal(cog, guild, "paypal", {
            "label": "PayPal", "value": "https://paypal.me/example", "note": "One time", "order": 2,
        })

        self.assertEqual(details.title_input.default, DEFAULT_TITLE)
        self.assertEqual(details.description_input.default, DEFAULT_DESCRIPTION)
        self.assertEqual(details.description_input.max_length, 4000)
        self.assertEqual(method.key_input.default, "paypal")
        self.assertEqual(method.order_input.default, "2")

    async def test_blank_card_details_do_not_save(self):
        cog, group = self.make_cog()

        ok, message = await cog.save_card_details(SimpleNamespace(id=1), " ", "Description", "Footer")

        self.assertFalse(ok)
        self.assertIn("cannot be empty", message)
        group.title.set.assert_not_awaited()

    async def test_method_save_validates_and_preserves_code_flag(self):
        cog, group = self.make_cog(methods={
            "btc": {"label": "Bitcoin", "value": "old", "note": "BTC", "code": True, "order": 1}
        })

        ok, _ = await cog.save_donation_method(
            SimpleNamespace(id=1), "btc", "btc", "Bitcoin", "new-address", "Network only", "3"
        )

        self.assertTrue(ok)
        saved = group.methods.value["btc"]
        self.assertTrue(saved["code"])
        self.assertEqual(saved["order"], 3)
        self.assertEqual(saved["value"], "new-address")

    async def test_method_rename_collision_is_rejected(self):
        cog, group = self.make_cog(methods={
            "paypal": {"label": "PayPal", "value": "one"},
            "patreon": {"label": "Patreon", "value": "two"},
        })

        ok, message = await cog.save_donation_method(
            SimpleNamespace(id=1), "paypal", "patreon", "PayPal", "new", "", "0"
        )

        self.assertFalse(ok)
        self.assertIn("already uses", message)
        group.methods.set.assert_not_awaited()

    async def test_note_add_edit_remove(self):
        cog, group = self.make_cog(notes=["First"])
        guild = SimpleNamespace(id=1)

        added, _ = await cog.save_donation_note(guild, "Second")
        edited, _ = await cog.save_donation_note(guild, "Updated", 0)
        removed, _ = await cog.remove_donation_note(guild, 1)

        self.assertTrue(added and edited and removed)
        self.assertEqual(group.notes.value, ["Updated"])

    def test_embed_budget_rejects_oversized_method(self):
        cog, _ = self.make_cog()
        valid, message = cog.validate_embed_budget({
            "title": "Donate", "description": "Help", "footer": "Thanks", "notes": [],
            "methods": {"large": {"label": "Large", "value": "v" * 1024, "note": "n"}},
        })

        self.assertFalse(valid)
        self.assertIn("too long", message)

    async def test_public_embed_uses_saved_settings(self):
        cog, group = self.make_cog(methods={"cash": {"label": "Cash App", "value": "$test"}})
        group.title.value = "Custom support"

        embed = await cog.donation_embed_for(SimpleNamespace(id=1), 0x123456)

        self.assertEqual(embed.title, "Custom support")
        self.assertEqual(embed.fields[0].name, "Cash App")
        self.assertEqual(embed.fields[0].value, "$test")

    async def test_cancel_keeps_settings_and_destructive_views_require_confirmation(self):
        cog, group = self.make_cog()
        cog.setup_embed = AsyncMock(return_value=MagicMock())
        author = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(manage_guild=True))
        interaction = SimpleNamespace(
            user=author, guild=SimpleNamespace(id=1),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        reset_view = ResetConfirmView(cog, author)
        method_view = MethodDeleteConfirmView(cog, author, "paypal")

        cancel = next(item for item in reset_view.children if item.label == "Cancel")
        await cancel.callback(interaction)

        group.clear.assert_not_awaited()
        self.assertEqual({item.label for item in reset_view.children}, {"Restore defaults", "Cancel"})
        self.assertEqual({item.label for item in method_view.children}, {"Remove method", "Cancel"})

    async def test_reset_uses_registered_defaults(self):
        cog, group = self.make_cog()

        await cog.reset_donation_settings(SimpleNamespace(id=1))

        group.clear.assert_awaited_once()

    async def test_interaction_owner_and_permission_checks(self):
        response = SimpleNamespace(send_message=AsyncMock())
        author = SimpleNamespace(id=1)
        stranger = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(manage_guild=True))
        interaction = SimpleNamespace(user=stranger, response=response)

        allowed = await owner_check(interaction, author)

        self.assertFalse(allowed)
        response.send_message.assert_awaited_once()

    def test_existing_text_commands_and_setup_are_registered(self):
        self.assertEqual(Donate.donateset.name, "donateset")
        self.assertIsNone(Donate.donateset.parent)
        names = {command.name for command in Donate.donateset.commands}
        self.assertTrue({
            "setup", "view", "title", "description", "footer", "method",
            "order", "remove", "note", "clear",
        }.issubset(names))


if __name__ == "__main__":
    unittest.main()
