import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mtg.art import MAX_CACHE_FILES, ScryfallArtCache, render_hand
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
            self.assertLessEqual(image.height,708)

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
