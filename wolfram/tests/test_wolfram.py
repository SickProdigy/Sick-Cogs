import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import aiohttp

from wolfram.wolfram import RequestFailure, Wolfram, WolframResponse


class FakeResponse:
    def __init__(self, status=200, body=b"ok", content_type="text/plain"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def read(self):
        return self._body


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def get(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response


class WolframRequestTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, session):
        cog = object.__new__(Wolfram)
        cog.session = session
        return cog

    async def test_success_returns_body_and_content_type(self):
        cog = self.make_cog(FakeSession(FakeResponse(body=b"answer", content_type="text/xml")))

        result = await cog._request("/v2/query", params={})

        self.assertEqual(result.body, b"answer")
        self.assertEqual(result.content_type, "text/xml")
        self.assertIsNone(result.failure)

    async def test_rejected_appid_is_distinct(self):
        cog = self.make_cog(FakeSession(FakeResponse(status=501)))

        result = await cog._request("/v2/query", params={"appid": "secret"})

        self.assertIs(result.failure, RequestFailure.AUTHENTICATION)
        self.assertEqual(result.status, 501)

    async def test_rate_limit_is_distinct(self):
        cog = self.make_cog(FakeSession(FakeResponse(status=429)))

        result = await cog._request("/v2/query", params={})

        self.assertIs(result.failure, RequestFailure.RATE_LIMIT)

    async def test_timeout_is_distinct(self):
        cog = self.make_cog(FakeSession(error=asyncio.TimeoutError()))

        result = await cog._request("/v2/query", params={})

        self.assertIs(result.failure, RequestFailure.TIMEOUT)

    async def test_network_failure_is_distinct(self):
        cog = self.make_cog(FakeSession(error=aiohttp.ClientConnectionError()))

        result = await cog._request("/v2/query", params={})

        self.assertIs(result.failure, RequestFailure.NETWORK)

    async def test_empty_question_shows_help_before_key_lookup(self):
        cog = object.__new__(Wolfram)
        cog._get_api_key = AsyncMock()
        ctx = SimpleNamespace(command=object(), send_help=AsyncMock())

        await Wolfram._wolfram.callback(cog, ctx)

        ctx.send_help.assert_awaited_once_with(ctx.command)
        cog._get_api_key.assert_not_awaited()

    async def test_primary_help_mentions_example_discovery(self):
        help_text = Wolfram._wolfram.help
        self.assertIn("[p]wolframexample [category]", help_text)
        self.assertIn("[p]wolframrandom", help_text)
        for category in ("mathematics", "science", "society", "everyday", "surprises"):
            self.assertIn(category, help_text)
        self.assertLess(len(help_text), 1024)

    async def test_provider_failure_is_distinct(self):
        cog = self.make_cog(FakeSession(FakeResponse(status=503)))

        result = await cog._request("/v2/query", params={})

        self.assertIs(result.failure, RequestFailure.PROVIDER)
        self.assertEqual(result.status, 503)

    async def test_missing_appid_keeps_setup_guidance(self):
        cog = object.__new__(Wolfram)
        cog.bot = SimpleNamespace(get_shared_api_tokens=AsyncMock(return_value={}))
        ctx = SimpleNamespace(clean_prefix="!", send=AsyncMock())

        result = await cog._get_api_key(ctx)

        self.assertIsNone(result)
        message = ctx.send.await_args.args[0]
        self.assertIn("No Wolfram|Alpha AppID is set", message)
        self.assertIn("!set api wolfram appid,APP_ID", message)

    async def test_auth_message_does_not_include_appid(self):
        cog = object.__new__(Wolfram)
        ctx = SimpleNamespace(clean_prefix="!", send=AsyncMock())

        await cog._send_request_failure(ctx, RequestFailure.AUTHENTICATION)

        message = ctx.send.await_args.args[0]
        self.assertIn("rejected", message)
        self.assertNotIn("secret", message)

class WolframExampleCatalogTests(unittest.TestCase):
    def test_bundled_catalog_has_all_supported_categories(self):
        from pathlib import Path
        from wolfram.wolfram import EXAMPLE_CATEGORY_URLS, load_examples

        catalog = load_examples(Path(__file__).parents[1] / "data" / "examples.json")
        self.assertEqual(set(catalog), set(EXAMPLE_CATEGORY_URLS))
        self.assertTrue(all(catalog.values()))

    def test_choose_example_rejects_unknown_category(self):
        cog = object.__new__(Wolfram)
        cog.examples = {"mathematics": ["2+2"]}
        cog._last_example = None
        with self.assertRaises(ValueError):
            cog.choose_example("unknown")
