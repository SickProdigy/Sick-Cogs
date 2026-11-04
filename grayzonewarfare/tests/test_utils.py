import asyncio
import unittest
from types import SimpleNamespace

from grayzonewarfare.grayzonewarfare import GrayZoneWarfare,QuestResultView,WikiUnavailable
from grayzonewarfare.utils import TTLCache,attach_guide,normalize_text,parse_guide_html,parse_page,rank_pages,record_page,source_url


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

    def test_guide_parser_extracts_steps_and_real_images(self):
        html="""<div id="toc"><p>Contents noise</p></div><h2>Guide</h2><p>Go to the airfield.</p><h3>First truck</h3><p>Use the panel.</p><img src="data:image/gif;base64,x" data-src="https://images.example/map.png" alt="Truck map"><img data-src="https://images.example/map.png" alt="duplicate">"""
        guide=parse_guide_html(html)
        self.assertEqual(guide["steps"],["Go to the airfield.","**First truck**","Use the panel."])
        self.assertEqual(guide["images"],[{"url":"https://images.example/map.png","caption":"Truck map"}])
        self.assertEqual(attach_guide(page(),guide)["walkthrough"][0],"Go to the airfield.")


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

    async def test_enrich_guide_uses_section_api_and_caches_result(self):
        sections={"parse":{"sections":[{"line":"Guide","index":"5"}]}}
        detail={"parse":{"text":{"*":"<h2>Guide</h2><p>Head north.</p><img data-src=\"https://images.example/map.png\" alt=\"Map\">"}}}
        cog=self.make_cog([FakeResponse(200,sections),FakeResponse(200,detail)])
        first=await cog.enrich_guide(page("A Single Drop"));second=await cog.enrich_guide(page("A Single Drop"))
        self.assertEqual(first["walkthrough"],["Head north."]);self.assertEqual(len(first["guide_images"]),1)
        self.assertEqual(second["walkthrough"],["Head north."]);self.assertEqual(len(cog.session.calls),2)
        self.assertEqual(cog.session.calls[1][1]["params"]["section"],"5")

    async def test_search_reports_http_and_shape_failures(self):
        with self.assertRaises(WikiUnavailable):await self.make_cog([FakeResponse(503,{})]).search_quests("First Recon")
        with self.assertRaises(WikiUnavailable):await self.make_cog([FakeResponse(200,{"data":{}})]).search_quests("First Recon")

    def test_embed_is_linked_attributed_and_bounded(self):
        value=attach_guide(page(briefing="Detailed sourced briefing "*100,objectives="Objective detail "*100),{"steps":["Go north.","Use the panel."],"images":[{"url":"https://images.example/one.png","caption":"Map"},{"url":"https://images.example/two.png","caption":"Panel"}]})
        embeds=GrayZoneWarfare.quest_embeds(value);embed=embeds[0]
        self.assertEqual(embed.url,value["fullurl"]);self.assertIn("Source: GZW Data API + Gray Zone Warfare Wiki",embed.footer.text)
        self.assertEqual(embed.fields[2].name,"Mission check");self.assertEqual(embed.fields[3].name,"How to complete it")
        self.assertEqual(len(embeds),2);self.assertTrue(all(len(field.value)<=1024 for field in embed.fields))

    def test_disambiguation_is_requester_scoped_and_bounded(self):
        view=QuestResultView(SimpleNamespace(),7,[page(f"Quest {index}") for index in range(8)])
        self.assertEqual(len(view.children[0].options),5);self.assertEqual(view.children[0].author_id,7)
