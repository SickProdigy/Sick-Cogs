import json
import unittest
from types import SimpleNamespace

from navidrome.lidarr import LidarrClient, LidarrError


class FakeContent:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()

    async def iter_chunked(self, size):
        for offset in range(0, len(self.data), size):
            yield self.data[offset:offset + size]


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.status = status
        self.headers = {}
        self.content = FakeContent(payload)
        self.connection = None

    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class LidarrClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_uses_header_auth_and_disables_redirects(self):
        session = FakeSession([FakeResponse({"version": "2.0"})])
        client = LidarrClient(session, "https://lidarr.example.com", "secret")

        result = await client.request("GET", "system/status")

        self.assertEqual(result["version"], "2.0")
        call = session.calls[0]
        self.assertEqual(call[1], "https://lidarr.example.com/api/v1/system/status")
        self.assertEqual(call[2]["headers"], {"X-Api-Key": "secret"})
        self.assertFalse(call[2]["allow_redirects"])
        self.assertNotIn("secret", call[1])

    async def test_redirect_and_auth_errors_are_safe(self):
        for status, message in ((302, "redirect"), (401, "API key")):
            with self.subTest(status=status):
                client = LidarrClient(
                    FakeSession([FakeResponse({}, status=status)]),
                    "https://lidarr.example.com", "secret",
                )
                with self.assertRaisesRegex(LidarrError, message):
                    await client.request("GET", "system/status")

    async def test_discovery_uses_root_folder_profile_defaults(self):
        session = FakeSession([
            FakeResponse({"version": "2.0"}),
            FakeResponse([{
                "path": "/music", "accessible": True,
                "defaultQualityProfileId": 3, "defaultMetadataProfileId": 4,
            }]),
            FakeResponse([{"id": 2, "name": "Standard"}, {"id": 3, "name": "Lossless"}]),
            FakeResponse([{"id": 4, "name": "Standard"}]),
        ])
        client = LidarrClient(session, "https://lidarr.example.com", "secret")
        result = await client.discover_configuration()
        self.assertEqual(result["root_folder_path"], "/music")
        self.assertEqual(result["quality_profile_id"], 3)
        self.assertEqual(result["metadata_profile_id"], 4)
        self.assertEqual(result["quality_profile_name"], "Lossless")
        self.assertEqual(result["metadata_profile_name"], "Standard")

    async def test_validation_checks_root_and_both_profiles(self):
        session = FakeSession([
            FakeResponse({"version": "2.0"}),
            FakeResponse([{"path": "/music"}]),
            FakeResponse([{"id": 3}]),
            FakeResponse([{"id": 4}]),
        ])
        client = LidarrClient(session, "https://lidarr.example.com", "secret")

        status = await client.validate_configuration(
            root_folder_path="/music", quality_profile_id=3, metadata_profile_id=4
        )

        self.assertEqual(status["version"], "2.0")
        self.assertEqual(len(session.calls), 4)

    async def test_validation_rejects_unknown_profile(self):
        session = FakeSession([
            FakeResponse({"version": "2.0"}), FakeResponse([{"path": "/music"}]),
            FakeResponse([{"id": 9}]), FakeResponse([{"id": 4}]),
        ])
        client = LidarrClient(session, "https://lidarr.example.com", "secret")
        with self.assertRaisesRegex(LidarrError, "quality profile"):
            await client.validate_configuration(
                root_folder_path="/music", quality_profile_id=3, metadata_profile_id=4
            )

    async def test_lookup_rejects_individual_tracks(self):
        client = LidarrClient(SimpleNamespace(), "https://lidarr.example.com", "secret")
        with self.assertRaisesRegex(LidarrError, "not individual tracks"):
            await client.lookup("track", "song")

    async def test_ensure_tag_reuses_case_insensitive_match(self):
        client = LidarrClient(
            FakeSession([FakeResponse([{"id": 8, "label": "Discord"}])]),
            "https://lidarr.example.com", "secret",
        )
        self.assertEqual(await client.ensure_tag("discord"), 8)
        self.assertEqual(len(client.session.calls), 1)

    async def test_update_artist_tags_preserves_unrelated_tags(self):
        session = FakeSession([FakeResponse({"id": 12})])
        client = LidarrClient(session, "https://lidarr.example.com", "secret")
        await client.update_artist_tags({"id": 12, "tags": [99]}, [1, 2])
        self.assertEqual(session.calls[0][2]["json"]["tags"], [1, 2, 99])
        self.assertEqual(session.calls[0][0], "PUT")

    async def test_artist_payload_applies_profiles_monitoring_and_tags(self):
        session = FakeSession([FakeResponse({"id": 12})])
        client = LidarrClient(session, "https://lidarr.example.com", "secret")
        await client.add_artist(
            {"artistName": "Example", "foreignArtistId": "mbid"},
            {"root_folder_path": "/music", "quality_profile_id": 3,
             "metadata_profile_id": 4, "monitor": "all"},
            [7, 7, 8],
        )
        payload = session.calls[0][2]["json"]
        self.assertEqual(payload["tags"], [7, 8])
        self.assertEqual(payload["rootFolderPath"], "/music")
        self.assertTrue(payload["addOptions"]["searchForMissingAlbums"])


if __name__ == "__main__":
    unittest.main()
