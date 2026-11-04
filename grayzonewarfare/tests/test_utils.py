import asyncio
import unittest
from types import SimpleNamespace

from grayzonewarfare.grayzonewarfare import GrayZoneWarfare,QuestResultView,WikiUnavailable
from grayzonewarfare.utils import TTLCache,normalize_text,parse_page,rank_pages,record_page,source_url


def record(name="First Recon",**changes):
    value={
        "id":normalize_text(name).replace(" ","-"),"name":name,"vendor":"Handshake",
        "location":"Kiu Vongsa , Nam Thaven , Pha Lang",
        "briefing":"Recon an enemy observation post and gather intelligence on the hostile forces.",
        "objectives":"Investigate the observation post and report your findings to Handshake",
    }
    value.update(changes);return value


def page(name="First Recon",**changes):
    return record_page(record(name,**changes),"2026-11-03T21:05:01.337678Z")


class UtilityTests(unittest.TestCase):
    def test_record_adapter_extracts_compact_sourced_guidance(self):
        parsed=parse_page(page())
        self.assertEqual(parsed["vendor"],"Handshake")
        self.assertEqual(parsed["location"],"Kiu Vongsa, Nam Thaven, Pha Lang")
        self.assertEqual(parsed["objectives"],["Investigate the observation post and report your findings to Handshake"])
        self.assertIn("gather intelligence",parsed["guide"])
        self.assertTrue(parsed["faction_specific"])
        self.assertEqual(parsed["updated"],"2026-11-03T21:05:01.337678Z")

    def test_source_url_is_direct_and_encoded(self):
        self.assertEqual(source_url("Jets N Marks"),"https://gray-zone-warfare.fandom.com/wiki/Jets_N_Marks")

    def test_ranking_handles_exact_partial_and_common_misspelling(self):
        pages=[page("Little Bird Down"),page("First Recon"),page("First Hit")]
        self.assertEqual(rank_pages("First Recon",pages)[0]["title"],"First Recon")
        self.assertEqual(rank_pages("recon",pages)[0]["title"],"First Recon")
        self.assertEqual(rank_pages("frist recon",pages)[0]["title"],"First Recon")

    def test_ranking_uses_objective_keywords_and_rejects_nonsense(self):
        pages=[page("A Single Drop",objectives="Check the fuel levels in two tanker trucks"),page("First Hit"),page("No Questions Asked")]
        self.assertEqual(rank_pages("tanker trucks",pages)[0]["title"],"A Single Drop")
        self.assertEqual(rank_pages("zzzz-no-real-quest-98765",pages),[])

    def test_cache_expires_and_evicts_oldest(self):
        now=[0.0];cache=TTLCache(ttl=10,max_entries=2,clock=lambda:now[0])
        cache.set("one",[1]);cache.set("two",[2]);self.assertEqual(cache.get("one"),[1])
        cache.set("three",[3]);self.assertIsNone(cache.get("two"))
        now[0]=9;cache.set("three",[3]);now[0]=11
        self.assertIsNone(cache.get("one"));self.assertEqual(cache.get("three"),[3])

    def test_normalization_handles_punctuation_and_accents(self):
        self.assertEqual(normalize_text("  Résumé: First-Recon! "),"resume first recon")


class FakeResponse:
    def __init__(self,status,payload):self.status=status;self.payload=payload
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return None
    async def json(self,content_type=None):return self.payload


class FakeSession:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,*args,**kwargs):self.calls.append((args,kwargs));return self.responses.pop(0)


class CogTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self,responses):
        cog=GrayZoneWarfare.__new__(GrayZoneWarfare);cog.cache=TTLCache();cog.request_lock=asyncio.Lock();cog.last_request=0;cog.session=FakeSession(responses);return cog

    async def test_search_fetches_catalog_once_then_ranks_locally(self):
        payload={"data":[record("Little Bird Down"),record()],"dataVersion":"2026-11-03T21:05:01Z"};cog=self.make_cog([FakeResponse(200,payload)])
        first=await cog.search_quests("First Recon");second=await cog.search_quests("frist recon")
        self.assertEqual(first[0]["title"],"First Recon");self.assertEqual(second[0]["title"],"First Recon");self.assertEqual(len(cog.session.calls),1)
        self.assertEqual(cog.session.calls[0][1]["params"],{"page":"1","per_page":"100"})

    async def test_search_collects_bounded_catalog_pages(self):
        first={"data":[record("Little Bird Down")],"dataVersion":"2026-11-03T21:05:01Z","totalPages":2}
        second={"data":[record("First Recon")],"dataVersion":"2026-11-03T21:05:01Z","totalPages":2}
        cog=self.make_cog([FakeResponse(200,first),FakeResponse(200,second)]);pages=await cog.search_quests("First Recon")
        self.assertEqual(pages[0]["title"],"First Recon");self.assertEqual(cog.session.calls[1][1]["params"],{"page":"2","per_page":"100"})

    async def test_search_reports_http_and_shape_failures(self):
        with self.assertRaises(WikiUnavailable):await self.make_cog([FakeResponse(503,{})]).search_quests("First Recon")
        with self.assertRaises(WikiUnavailable):await self.make_cog([FakeResponse(200,{"data":{}})]).search_quests("First Recon")

    def test_embed_is_linked_attributed_and_bounded(self):
        value=page(briefing="Detailed sourced briefing "*100,objectives="Objective detail "*100)
        embed=GrayZoneWarfare.quest_embed(value)
        self.assertEqual(embed.url,value["fullurl"]);self.assertIn("Source: GZW Data API + Gray Zone Warfare Wiki",embed.footer.text)
        self.assertTrue(all(len(field.value)<=1024 for field in embed.fields))

    def test_disambiguation_is_requester_scoped_and_bounded(self):
        view=QuestResultView(SimpleNamespace(),7,[page(f"Quest {index}") for index in range(8)])
        self.assertEqual(len(view.children[0].options),5);self.assertEqual(view.children[0].author_id,7)
