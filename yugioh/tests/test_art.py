import io,unittest
from PIL import Image
from yugioh.art import ASSETS,render_field,render_hand
from yugioh.cards import CARDS
from yugioh.engine import Game
class ArtTests(unittest.TestCase):
    def test_assets_and_private_render_are_bounded(self):
        for name in ("duel_table.png","hand_tray.png","card_back.png"):self.assertTrue((ASSETS/name).is_file())
        cards=list(CARDS.values())[:6];out=render_hand(cards,[None]*6);self.assertLess(len(out.getvalue()),8*1024*1024)
        with Image.open(io.BytesIO(out.getvalue())) as image:self.assertEqual(image.size,(1200,800))
    def test_public_field_is_bounded(self):
        out=render_field(Game(1,[10,20],1));self.assertLess(len(out.getvalue()),8*1024*1024)
        with Image.open(io.BytesIO(out.getvalue())) as image:self.assertEqual(image.size,(1200,675))
if __name__=="__main__":unittest.main()
