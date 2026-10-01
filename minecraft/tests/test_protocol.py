import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from minecraft.network import address_allowed, java_target, normalize_host
from minecraft.protocol import (
    BEDROCK_MAGIC,
    MinecraftQueryError,
    encode_varint,
    flatten_description,
    parse_bedrock_pong,
    parse_java_status,
)


class JavaProtocolTests(unittest.TestCase):
    def test_varint_encoding(self):
        self.assertEqual(encode_varint(0), b"\x00")
        self.assertEqual(encode_varint(255), b"\xff\x01")
        self.assertEqual(encode_varint(-1), b"\xff\xff\xff\xff\x0f")

    def test_parses_java_status_and_chat_description(self):
        payload = json.dumps({
            "version": {"name": "1.21.1", "protocol": 767},
            "players": {
                "online": 2,
                "max": 20,
                "sample": [{"name": "Alex"}, {"name": "Steve"}],
            },
            "description": {"text": "Sick", "extra": [{"text": " Gaming"}]},
        }).encode()
        result = parse_java_status(payload)
        self.assertEqual(result["motd"], "Sick Gaming")
        self.assertEqual(result["players"], 2)
        self.assertEqual(result["sample"], ["Alex", "Steve"])

    def test_rejects_invalid_java_json(self):
        with self.assertRaises(MinecraftQueryError):
            parse_java_status(b"not json")


class BedrockProtocolTests(unittest.TestCase):
    def test_parses_unconnected_pong(self):
        value = "MCPE;SickGaming;800;1.21.0;3;20;123;Second line;Survival;1;19132;19133;"
        encoded = value.encode()
        packet = (
            b"\x1c"
            + struct.pack(">q", 1000)
            + struct.pack(">q", 123)
            + BEDROCK_MAGIC
            + struct.pack(">H", len(encoded))
            + encoded
        )
        result = parse_bedrock_pong(packet)
        self.assertEqual(result["motd"], "SickGaming")
        self.assertEqual(result["players"], 3)
        self.assertEqual(result["max_players"], 20)
        self.assertEqual(result["game_mode"], "Survival")

    def test_rejects_invalid_pong(self):
        with self.assertRaises(MinecraftQueryError):
            parse_bedrock_pong(b"invalid")


class NetworkPolicyTests(unittest.TestCase):
    def test_normalizes_hosts_and_rejects_urls(self):
        self.assertEqual(normalize_host("Play.Example.COM."), "play.example.com")
        with self.assertRaises(ValueError):
            normalize_host("https://play.example.com")

    def test_private_addresses_require_approval(self):
        self.assertTrue(address_allowed("8.8.8.8"))
        self.assertFalse(address_allowed("10.1.2.3"))
        self.assertTrue(address_allowed("10.1.2.3", private_allowed=True))
        self.assertFalse(address_allowed("127.0.0.1"))


class SRVTests(unittest.IsolatedAsyncioTestCase):
    async def test_java_srv_uses_lowest_priority_then_highest_weight(self):
        answers = [
            SimpleNamespace(priority=10, weight=5, target="later.example.", port=25570),
            SimpleNamespace(priority=5, weight=1, target="first.example.", port=25566),
            SimpleNamespace(priority=5, weight=10, target="weighted.example.", port=25567),
        ]
        with patch("minecraft.network.dns.asyncresolver.resolve", AsyncMock(return_value=answers)):
            self.assertEqual(
                await java_target("play.example.com", None),
                ("weighted.example", 25567, "play.example.com"),
            )

    async def test_java_srv_failure_falls_back_to_default_port(self):
        with patch(
            "minecraft.network.dns.asyncresolver.resolve",
            AsyncMock(side_effect=TimeoutError),
        ):
            self.assertEqual(
                await java_target("play.example.com", None),
                ("play.example.com", 25565, "play.example.com"),
            )
