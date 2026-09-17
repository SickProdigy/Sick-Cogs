import base64
import json
import unittest

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from calendarevents.google import GoogleCalendarClient, SCOPE, TOKEN_URL, service_assertion


def decode(segment):
    segment += "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment))


class GoogleCalendarClientTests(unittest.TestCase):
    def test_service_assertion_has_expected_bounded_claims(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        header, claims, signature = service_assertion("calendar@example.test", pem, now=1000).split(".")
        self.assertEqual(decode(header), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(decode(claims), {
            "iss": "calendar@example.test", "scope": SCOPE, "aud": TOKEN_URL,
            "iat": 1000, "exp": 4600,
        })
        self.assertTrue(signature)

    def test_calendar_ids_are_quoted_as_one_path_segment(self):
        self.assertEqual(
            GoogleCalendarClient.calendar_path("team/calendar@example.com", "/events"),
            "/calendars/team%2Fcalendar%40example.com/events",
        )
