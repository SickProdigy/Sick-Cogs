import unittest
from unittest.mock import AsyncMock

from mybb.membership import ExternalSignInClient, MembershipError


class ExternalSignInContractTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return ExternalSignInClient("https://forum.example/community", "cog", "secret")

    async def test_start_link_uses_provider_contract_and_bounds_polling(self):
        client = self.client()
        client.request = AsyncMock(return_value={
            "device_code": "device", "user_code": "ABCD-EFGH",
            "verification_uri_complete": "https://forum.example/community/external-signin/device?user_code=ABCD-EFGH",
            "expires_in": 5000, "interval": 1,
        })
        result = await client.start_link(123, "guild:456")
        client.request.assert_awaited_once_with("/device/authorize", {
            "discord_user_id": "123", "context": "guild:456",
        })
        self.assertEqual(result["expires_in"], 600)
        self.assertEqual(result["interval"], 5)

    async def test_rejects_foreign_verification_url(self):
        client = self.client()
        client.request = AsyncMock(return_value={
            "device_code": "device", "user_code": "ABCD-EFGH",
            "verification_uri_complete": "https://evil.example/device?user_code=ABCD-EFGH",
            "expires_in": 600, "interval": 5,
        })
        with self.assertRaisesRegex(MembershipError, "unsafe"):
            await client.start_link(123, "guild:456")

    async def test_active_membership_returns_validated_entitlement_set(self):
        client = self.client()
        client.request = AsyncMock(return_value={
            "issuer": client.issuer, "sub": "42", "discord_user_id": "123",
            "context": "guild:456", "checked_at": 1, "status": "active",
            "entitlements": ["vip", "media.access"],
        })
        result = await client.membership("link-token")
        self.assertEqual(result["entitlements"], {"vip", "media.access"})
        client.request.assert_awaited_once_with("/membership", {"link_token": "link-token"})

    async def test_only_explicit_inactive_states_allow_empty_snapshot(self):
        for status in ("inactive", "unlinked"):
            with self.subTest(status=status):
                client = self.client()
                client.request = AsyncMock(return_value={"status": status, "entitlements": []})
                result = await client.membership("link-token")
                self.assertEqual(result["entitlements"], set())

    async def test_ambiguous_or_malformed_states_are_errors(self):
        for body in (
            {"status": "error", "entitlements": []},
            {"status": "active"},
            {"status": "active", "entitlements": "vip"},
            {"status": "inactive", "entitlements": ["vip"]},
            {"status": "active", "entitlements": ["bad entitlement"]},
        ):
            with self.subTest(body=body):
                client = self.client()
                client.request = AsyncMock(return_value=body)
                with self.assertRaises(MembershipError):
                    await client.membership("link-token")

    async def test_unlink_uses_durable_link_credential(self):
        client = self.client()
        client.request = AsyncMock(return_value={})
        await client.unlink("link-token")
        client.request.assert_awaited_once_with("/unlink", {"link_token": "link-token"})


if __name__ == "__main__":
    unittest.main()
