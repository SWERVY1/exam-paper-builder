import unittest

from exam_image_splitter.ocr_fallback import (
    WindowsOcrUnavailable,
    _anchors_from_ocr_records,
    _parse_number_glyph,
)


class WindowsOcrFallbackTests(unittest.TestCase):
    def test_rejects_a_recognized_number_that_does_not_match_its_slot(self) -> None:
        records = []
        for number in range(1, 31):
            records.append(
                {
                    "page": (number + 1) // 2,
                    "text": f"{number}." if number != 29 else "四.",
                    "x": 0.1 if number % 2 else 0.52,
                    "y": 0.14,
                    "width": 0.02,
                    "height": 0.02,
                }
            )

        with self.assertRaisesRegex(WindowsOcrUnavailable, "slot 29"):
            _anchors_from_ocr_records(records)

    def test_rejects_an_incomplete_slot_sequence(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not a complete 20 or 30"):
            _anchors_from_ocr_records([])

    def test_accepts_a_complete_twenty_question_sequence(self) -> None:
        records = [
            {
                "page": (number + 4) // 5,
                "text": f"{number}.",
                "x": 0.1 if (number - 1) % 5 < 3 else 0.52,
                "y": 0.14 + ((number - 1) % 5 if (number - 1) % 5 < 3 else (number - 1) % 5 - 3) * 0.2,
                "width": 0.02,
                "height": 0.02,
            }
            for number in range(1, 21)
        ]

        anchors = _anchors_from_ocr_records(records)

        self.assertEqual([item["number"] for item in anchors], list(range(1, 21)))
        self.assertTrue(all(item["confidence"] == "needs_review" for item in anchors))

    def test_expected_count_can_be_declared_explicitly(self) -> None:
        records = [
            {
                "page": number,
                "text": f"{number}.",
                "x": 0.1,
                "y": 0.14,
                "width": 0.02,
                "height": 0.02,
            }
            for number in range(1, 46)
        ]
        anchors = _anchors_from_ocr_records(records, expected_counts=(45,))
        self.assertEqual(len(anchors), 45)

    def test_parses_common_chinese_number_forms_without_guessing(self) -> None:
        self.assertEqual(_parse_number_glyph("十."), 10)
        self.assertEqual(_parse_number_glyph("二十九·"), 29)
        self.assertIsNone(_parse_number_glyph("四十十"))


if __name__ == "__main__":
    unittest.main()
