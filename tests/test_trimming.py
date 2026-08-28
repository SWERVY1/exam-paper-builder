import unittest

from PIL import Image, ImageDraw

from exam_image_splitter.build import (
    _trim_leading_context,
    _trim_narrow_page_footer,
    _trim_trailing_context,
    bottom_edge_ink_metrics,
    edge_ink_metrics,
    outer_edge_sides,
    should_warn_bottom_edge,
)


class LeadingContextTrimTests(unittest.TestCase):
    def test_removes_context_before_anchor_separator(self) -> None:
        image = Image.new("RGB", (600, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 0, 580, 55), fill="black")
        draw.rectangle((20, 150, 580, 210), fill="black")

        trimmed = _trim_leading_context(image, anchor_hint_px=150, dpi=300)
        try:
            self.assertLess(trimmed.height, image.height)
            self.assertGreaterEqual(trimmed.height, 340)
            self.assertLessEqual(trimmed.height, 380)
        finally:
            trimmed.close()
            image.close()


class EdgeInkTests(unittest.TestCase):
    def test_detects_ink_cut_by_outer_edge(self) -> None:
        image = Image.new("RGB", (100, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 40, 8, 120), fill="black")
        try:
            metrics = edge_ink_metrics(image, "left")
            self.assertGreater(metrics["ink_ratio"], 0.3)
            self.assertGreater(metrics["contact_row_ratio"], 0.3)
        finally:
            image.close()

    def test_ignores_ink_with_a_safe_outer_margin(self) -> None:
        image = Image.new("RGB", (100, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 40, 90, 120), fill="black")
        try:
            self.assertEqual(edge_ink_metrics(image, "left")["ink_ratio"], 0.0)
            self.assertEqual(edge_ink_metrics(image, "right")["ink_ratio"], 0.0)
        finally:
            image.close()

    def test_detects_content_cut_by_bottom_edge(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 70, 170, 99), fill="black")
        try:
            metrics = bottom_edge_ink_metrics(image)
            self.assertGreater(metrics["ink_ratio"], 0.5)
            self.assertGreater(metrics["contact_column_ratio"], 0.5)
        finally:
            image.close()

    def test_bottom_edge_check_accepts_blank_margin(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 50, 170, 80), fill="black")
        try:
            metrics = bottom_edge_ink_metrics(image)
            self.assertEqual(metrics["ink_ratio"], 0.0)
            self.assertEqual(metrics["contact_column_ratio"], 0.0)
        finally:
            image.close()

    def test_only_page_ending_crop_escalates_bottom_edge_ink(self) -> None:
        metrics = {"ink_ratio": 0.03, "contact_column_ratio": 0.06}
        self.assertFalse(
            should_warn_bottom_edge(
                {"trim_trailing_context": False},
                metrics,
                max_ink_ratio=0.02,
                max_contact_column_ratio=0.05,
            )
        )
        self.assertTrue(
            should_warn_bottom_edge(
                {"trim_trailing_context": True},
                metrics,
                max_ink_ratio=0.02,
                max_contact_column_ratio=0.05,
            )
        )

    def test_checks_only_the_physical_outer_edge_for_each_column(self) -> None:
        self.assertEqual(outer_edge_sides([0.055, 0.1, 0.495, 0.9]), ("left",))
        self.assertEqual(outer_edge_sides([0.505, 0.1, 0.945, 0.9]), ("right",))
        self.assertEqual(
            outer_edge_sides([0.055, 0.1, 0.945, 0.9]),
            ("left", "right"),
        )

    def test_removes_small_footer_after_bottom_separator(self) -> None:
        image = Image.new("RGB", (600, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 580, 850), fill="black")
        draw.rectangle((20, 1050, 580, 1120), fill="black")

        trimmed = _trim_trailing_context(
            image,
            page_height_px=1200,
            crop_top_y=0.0,
            dpi=300,
        )
        try:
            self.assertLess(trimmed.height, 1000)
            self.assertGreater(trimmed.height, 850)
        finally:
            trimmed.close()
            image.close()

    def test_removes_an_entire_multiline_confirmation_footer(self) -> None:
        image = Image.new("RGB", (600, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 580, 850), fill="black")
        # The footer has its own title and body separated by a blank line.
        # The crop must choose the separator above the footer, not its later
        # internal blank line.
        draw.rectangle((20, 960, 580, 990), fill="black")
        draw.rectangle((20, 1040, 580, 1100), fill="black")

        trimmed = _trim_trailing_context(
            image,
            page_height_px=1200,
            crop_top_y=0.0,
            dpi=300,
        )
        try:
            self.assertLess(trimmed.height, 950)
            self.assertGreater(trimmed.height, 850)
        finally:
            trimmed.close()
            image.close()

    def test_keeps_large_bottom_diagram(self) -> None:
        image = Image.new("RGB", (600, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 580, 650), fill="black")
        draw.rectangle((20, 850, 580, 1150), fill="black")

        trimmed = _trim_trailing_context(
            image,
            page_height_px=1200,
            crop_top_y=0.0,
            dpi=300,
        )
        try:
            self.assertEqual(trimmed.size, image.size)
        finally:
            if trimmed is not image:
                trimmed.close()
            image.close()

    def test_removes_detached_narrow_page_number(self) -> None:
        image = Image.new("RGB", (600, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 580, 1020), fill="black")
        draw.rectangle((0, 1100, 70, 1155), fill="black")

        trimmed = _trim_narrow_page_footer(
            image,
            page_height_px=1200,
            crop_top_y=0.0,
            dpi=300,
        )
        try:
            self.assertLess(trimmed.height, 1090)
            self.assertGreater(trimmed.height, 1020)
        finally:
            if trimmed is not image:
                trimmed.close()
            image.close()

    def test_keeps_wide_second_row_of_choices(self) -> None:
        image = Image.new("RGB", (600, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 580, 1020), fill="black")
        draw.rectangle((40, 1100, 130, 1150), fill="black")
        draw.rectangle((360, 1100, 500, 1150), fill="black")

        trimmed = _trim_narrow_page_footer(
            image,
            page_height_px=1200,
            crop_top_y=0.0,
            dpi=300,
        )
        try:
            self.assertEqual(trimmed.size, image.size)
        finally:
            if trimmed is not image:
                trimmed.close()
            image.close()

    def test_removes_detached_blue_copyright_footer(self) -> None:
        image = Image.new("RGB", (600, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 580, 940), fill="black")
        draw.rectangle((30, 1040, 105, 1090), fill="black")
        draw.rectangle((145, 1105, 540, 1145), fill="blue")

        trimmed = _trim_narrow_page_footer(
            image,
            page_height_px=1200,
            crop_top_y=0.1,
            dpi=300,
        )
        try:
            self.assertLess(trimmed.height, 1010)
        finally:
            if trimmed is not image:
                trimmed.close()
            image.close()

    def test_keeps_image_when_no_prior_context_exists(self) -> None:
        image = Image.new("RGB", (600, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 145, 580, 210), fill="black")

        trimmed = _trim_leading_context(image, anchor_hint_px=150, dpi=300)
        try:
            self.assertEqual(trimmed.size, image.size)
        finally:
            if trimmed is not image:
                trimmed.close()
            image.close()


if __name__ == "__main__":
    unittest.main()
