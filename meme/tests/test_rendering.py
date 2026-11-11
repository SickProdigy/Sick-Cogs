import io
import unittest

from PIL import Image

from meme.memeify import Memeify


class MemeRenderingTests(unittest.TestCase):
    def test_transparent_source_renders_captions_as_png(self):
        source = io.BytesIO()
        Image.new("RGBA", (320, 240), (40, 80, 120, 0)).save(source, "PNG")
        output = Memeify._render(source.getvalue(), "TOP", "BOTTOM", True)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (320, 440))
            image.load()

    def test_animated_input_preserves_first_frame_behavior(self):
        source = io.BytesIO()
        frames = [Image.new("RGB", (320, 240), color) for color in ("red", "blue")]
        frames[0].save(source, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
        output = Memeify._render(source.getvalue(), "", "", False)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.getpixel((160, 120)), (255, 0, 0))
            self.assertFalse(getattr(image, "is_animated", False))

    def test_malformed_image_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "could not be read"):
            Memeify._render(b"not an image", "TOP", "BOTTOM", False)
