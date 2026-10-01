import struct
import unittest

from gmod.query import A2SError, address_allowed, normalize_host, parse_info_packet


def cstring(value):
    return value.encode() + b"\x00"


class QueryParserTests(unittest.TestCase):
    def packet(self, *, edf=0):
        payload = (
            b"\xff\xff\xff\xffI"
            + struct.pack("<B", 17)
            + cstring("SickGaming GMod")
            + cstring("gm_construct")
            + cstring("garrysmod")
            + cstring("Garry's Mod")
            + struct.pack("<HBBBccBB", 4000, 12, 32, 2, b"d", b"l", 0, 1)
            + cstring("2026.10")
            + struct.pack("<B", edf)
        )
        return payload

    def test_parses_core_info_fields(self):
        result = parse_info_packet(self.packet())
        self.assertEqual(result["name"], "SickGaming GMod")
        self.assertEqual(result["map"], "gm_construct")
        self.assertEqual(result["app_id"], 4000)
        self.assertEqual(result["players"], 12)
        self.assertEqual(result["max_players"], 32)
        self.assertEqual(result["bots"], 2)
        self.assertTrue(result["vac"])
        self.assertFalse(result["password"])

    def test_parses_optional_edf_fields(self):
        packet = self.packet(edf=0x80 | 0x20 | 0x01)
        packet += struct.pack("<H", 27015) + cstring("sandbox,secure") + struct.pack("<Q", 4000)
        result = parse_info_packet(packet)
        self.assertEqual(result["port"], 27015)
        self.assertEqual(result["keywords"], "sandbox,secure")
        self.assertEqual(result["game_id"], "4000")

    def test_rejects_split_and_truncated_packets(self):
        with self.assertRaises(A2SError):
            parse_info_packet(b"\xfe\xff\xff\xffsplit")
        with self.assertRaises(A2SError):
            parse_info_packet(self.packet()[:-4])


class HostPolicyTests(unittest.TestCase):
    def test_normalizes_host_without_accepting_urls(self):
        self.assertEqual(normalize_host("Play.Example.COM."), "play.example.com")
        with self.assertRaises(ValueError):
            normalize_host("https://play.example.com/path")

    def test_public_addresses_allowed_and_sensitive_ranges_blocked(self):
        self.assertTrue(address_allowed("8.8.8.8"))
        for address in ("127.0.0.1", "169.254.1.1", "::1", "224.0.0.1"):
            with self.subTest(address=address):
                self.assertFalse(address_allowed(address))

    def test_private_addresses_require_approval(self):
        self.assertFalse(address_allowed("192.168.1.20"))
        self.assertTrue(address_allowed("192.168.1.20", private_allowed=True))
