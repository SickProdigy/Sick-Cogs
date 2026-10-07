import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mtg.art import MAX_CACHE_FILES, ScryfallArtCache, match_result, render_battlefield, render_hand
from mtg.engine import Game, Permanent
from mtg.cards import CARDS


class ArtTests(unittest.TestCase):
    def test_placeholder_hand_render_is_bounded_png(self):
        cards=list(CARDS.values())[:8]
        output=render_hand(cards,[None]*len(cards),0)
        payload=output.getvalue()
        self.assertLess(len(payload),8*1024*1024)
        with Image.open(io.BytesIO(payload)) as image:
            self.assertEqual(image.format,"PNG")
            self.assertLessEqual(image.width,964)
            self.assertLessEqual(image.height,726)

    def test_hand_position_label_does_not_cover_card_art(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"card.jpg"
            Image.new("RGB",(220,307),(255,255,255)).save(path,format="JPEG")
            output=render_hand([next(iter(CARDS.values()))],[path],0)
            with Image.open(output) as image:
                self.assertEqual(image.getpixel((43,99)),(255,255,255))
                self.assertNotEqual(image.getpixel((126,56)),(255,255,255))

    def test_hand_bezels_distinguish_playable_and_neutral_cards(self):
        cards=list(CARDS.values())[:2]
        output=render_hand(cards,[None,None],0,{1})
        with Image.open(output) as image:
            self.assertEqual(image.getpixel((126,41)),(222,185,82))
            self.assertEqual(image.getpixel((362,41)),(151,103,219))

    def test_finished_match_result_names_winner_loser_and_cause(self):
        game=Game(1,[10,20],1,ai_user=20,ai_difficulty="easy")
        game.winner=10; game.finished_reason="zero life"; game.phase="finished"
        self.assertEqual(
            match_result(game,{10:"SickProdigy",20:"SGbot (Easy AI)"}),
            ("VICTORY","SickProdigy","Defeated SGbot (Easy AI)","SGbot (Easy AI) reached zero life"),
        )

    def test_public_battlefield_render_is_bounded_png(self):
        game=Game(1,[10,20],1)
        creature=next(uid for uid,key in game.cards.items() if key=="lea:164")
        game.players[10].battlefield=[Permanent(creature,"goblin",sick=False)]
        background=Path(__file__).parents[1]/"assets"/"default_playmat.png"
        output=render_battlefield(game,{10:"First player",20:"Second player"},{},background)
        payload=output.getvalue()
        self.assertLess(len(payload),8*1024*1024)
        with Image.open(io.BytesIO(payload)) as image:
            self.assertEqual(image.format,"PNG")
            self.assertEqual(image.size,(1280,853))

    def test_valid_card_image_is_cached_without_reencoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/"card.jpg"
            source=io.BytesIO()
            Image.new("RGB",(488,680),(80,120,90)).save(source,format="JPEG")
            ScryfallArtCache._validate_and_write(path,source.getvalue())
            self.assertEqual(path.read_bytes(),source.getvalue())

    def test_cache_trims_oldest_files_by_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); cache=ScryfallArtCache(root,None)
            for number in range(MAX_CACHE_FILES+1):
                path=root/f"{number}.jpg"; path.write_bytes(b"x")
                path.touch()
            cache._trim()
            self.assertEqual(len(list(root.glob("*.jpg"))),MAX_CACHE_FILES)


if __name__=="__main__":
    unittest.main()
