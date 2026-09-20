import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from roletools.roletools import RoleTools
from roletools.setup import RoleToolsSetupView
from roletools.shop import RoleShopManagerView, RoleShopOfferManagerView, ShopConfirmView


class Value:
    def __init__(self, value):
        self.value = value
        self.set = AsyncMock(side_effect=self._set)

    async def __call__(self):
        return self.value

    async def _set(self, value):
        self.value = value


class RoleShopTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, role_ids=None):
        shop = Value({"role_ids": role_ids or [], "channel_id": None, "message_id": None})
        cog = object.__new__(RoleTools)
        cog.config = SimpleNamespace(
            guild=MagicMock(return_value=SimpleNamespace(role_shop=shop))
        )
        cog.settings = {}
        return cog, shop

    def test_setup_dashboard_exposes_role_shop(self):
        view = RoleToolsSetupView(SimpleNamespace(), SimpleNamespace(id=1))
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        self.assertIn("Role shop", labels)

    def test_manager_has_publish_lifecycle_controls(self):
        view = RoleShopManagerView(SimpleNamespace(), SimpleNamespace(id=1))
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        self.assertEqual(labels, {"Manage shop roles", "Unpublish", "Done"})
        self.assertEqual(len(view.children), 4)

    def test_offer_manager_separates_role_editing_from_shop_dashboard(self):
        view = RoleShopOfferManagerView(SimpleNamespace(), SimpleNamespace(id=1))
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        placeholders = {
            item.placeholder for item in view.children if getattr(item, "placeholder", None)
        }
        self.assertEqual(labels, {"Back to shop"})
        self.assertEqual(
            placeholders,
            {"Configure or add a shop offer", "Remove shop offers"},
        )

    async def test_publish_selector_resolves_app_command_channel(self):
        channel = SimpleNamespace(id=55)
        guild = SimpleNamespace(get_channel=MagicMock(return_value=channel))
        cog = SimpleNamespace(
            publish_role_shop=AsyncMock(return_value=(True, "published")),
            shop_manager_embed=AsyncMock(return_value=MagicMock()),
        )
        view = RoleShopManagerView(cog, SimpleNamespace(id=1))
        selector = next(
            item for item in view.children
            if getattr(item, "placeholder", "") == "Publish or move shop to a channel"
        )
        selector._values = [SimpleNamespace(id=55)]
        interaction = SimpleNamespace(
            guild=guild,
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await selector.callback(interaction)

        guild.get_channel.assert_called_once_with(55)
        cog.publish_role_shop.assert_awaited_once_with(guild, channel)
        interaction.edit_original_response.assert_awaited_once()

    async def test_configure_offer_promotes_role_and_saves_price_and_duration(self):
        cog, _ = self.make_cog()
        cost = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
        duration = SimpleNamespace(set=AsyncMock(), clear=AsyncMock())
        cog.config.role = MagicMock(return_value=SimpleNamespace(cost=cost, duration=duration))
        cog.update_role_catalog = AsyncMock(return_value=(1, []))
        cog.restricted_role_ids = AsyncMock(return_value=[2])
        cog.private_group_for_gateway = AsyncMock(return_value=(None, None))
        cog.update_shop_roles = AsyncMock(return_value=(1, []))
        role = SimpleNamespace(id=2, mention="<@&2>")

        with patch("roletools.shop.bank.get_max_balance", AsyncMock(return_value=10000)):
            ok, message = await cog.configure_shop_offer(
                SimpleNamespace(id=1), role, 100, 10
            )

        self.assertTrue(ok)
        self.assertIn("Added", message)
        cog.update_role_catalog.assert_awaited_once()
        cost.set.assert_awaited_once_with(100)
        duration.set.assert_awaited_once_with(600)
        cog.update_shop_roles.assert_awaited_once()

    async def test_only_advanced_roles_can_be_offered(self):
        cog, shop = self.make_cog()
        cog.restricted_role_ids = AsyncMock(return_value=[2])
        cog.sync_role_shop = AsyncMock()
        guild = SimpleNamespace(id=1)
        roles = [SimpleNamespace(id=1, name="Basic"), SimpleNamespace(id=2, name="VIP")]

        changed, notes = await cog.update_shop_roles(guild, roles, add=True)

        self.assertEqual(changed, 1)
        self.assertEqual(shop.value["role_ids"], [2])
        self.assertIn("Advanced roles", notes[0])

    async def test_purchase_rechecks_offer_and_uses_shared_transaction(self):
        role = SimpleNamespace(id=2, mention="<@&2>")
        member = SimpleNamespace(guild=SimpleNamespace(id=1))
        cog, _ = self.make_cog([2])
        cog.shop_role_ids = AsyncMock(return_value=[2])
        cog.private_group_for_gateway = AsyncMock(return_value=(None, None))
        cog.give_roles = AsyncMock(return_value=[])

        ok, _ = await cog.purchase_shop_role(member, role)

        self.assertTrue(ok)
        cog.give_roles.assert_awaited_once_with(member, [role], "Role shop purchase")

    async def test_gateway_purchase_uses_private_group_join(self):
        role = SimpleNamespace(id=2, mention="<@&2>")
        member = SimpleNamespace(guild=SimpleNamespace(id=1))
        cog, _ = self.make_cog([2])
        cog.shop_role_ids = AsyncMock(return_value=[2])
        cog.private_group_for_gateway = AsyncMock(return_value=("vip", {}))
        cog.join_private_group = AsyncMock(return_value=(True, "joined"))

        result = await cog.purchase_shop_role(member, role)

        self.assertEqual(result, (True, "You purchased <@&2>."))
        cog.join_private_group.assert_awaited_once_with(member, "vip")

    async def test_confirmation_prevents_repeat_charge(self):
        author = SimpleNamespace(id=1)
        cog = SimpleNamespace(purchase_shop_role=AsyncMock(return_value=(True, "done")))
        view = ShopConfirmView(cog, author, SimpleNamespace(id=2))
        response = SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock())
        interaction = SimpleNamespace(
            user=author, response=response, edit_original_response=AsyncMock()
        )

        await view.confirm.callback(interaction)
        await view.confirm.callback(interaction)

        cog.purchase_shop_role.assert_awaited_once()
        response.send_message.assert_awaited_once()
