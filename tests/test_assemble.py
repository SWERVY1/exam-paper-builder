import unittest

from PIL import Image

from exam_image_splitter.assemble import AssemblyConfig, assemble_vertical


class AssemblyTests(unittest.TestCase):
    def test_preserves_piece_order_and_normalizes_width(self) -> None:
        red = Image.new("RGB", (100, 50), "red")
        blue = Image.new("RGB", (50, 50), "blue")
        try:
            config = AssemblyConfig(
                output_width_px=240,
                padding_px=20,
                gap_px=10,
                max_output_height_px=0,
            )
            result = assemble_vertical([red, blue], config)
            self.assertEqual(len(result), 1)
            output = result[0]
            self.assertEqual(output.width, 240)
            self.assertEqual(output.getpixel((120, 30)), (255, 0, 0))
            self.assertEqual(output.getpixel((120, 145)), (0, 0, 255))
            output.close()
        finally:
            red.close()
            blue.close()

    def test_splits_oversized_output(self) -> None:
        piece = Image.new("RGB", (100, 400), "black")
        try:
            config = AssemblyConfig(
                output_width_px=140,
                padding_px=20,
                gap_px=0,
                max_output_height_px=300,
            )
            result = assemble_vertical([piece], config)
            self.assertGreater(len(result), 1)
            self.assertTrue(all(part.height <= 300 for part in result))
            for part in result:
                part.close()
        finally:
            piece.close()


if __name__ == "__main__":
    unittest.main()

