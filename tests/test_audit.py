import unittest

from exam_image_splitter.audit import classify_anchor_result


class AuditClassificationTests(unittest.TestCase):
    def test_accepts_complete_four_page_twenty_question_exam(self) -> None:
        self.assertEqual(
            classify_anchor_result(4, 20, True, 0), "proposal_candidate"
        )

    def test_rejects_accidental_small_sequence(self) -> None:
        self.assertEqual(
            classify_anchor_result(4, 3, True, 0), "needs_anchor_review"
        )

    def test_rejects_four_page_non_twenty_count(self) -> None:
        self.assertEqual(
            classify_anchor_result(4, 19, True, 0), "needs_anchor_review"
        )

    def test_routes_empty_text_or_anchors_to_structure_ocr(self) -> None:
        self.assertEqual(
            classify_anchor_result(16, 0, False, 0), "needs_structure_ocr"
        )

    def test_routes_complete_geometry_only_ocr_sequence_to_review(self) -> None:
        self.assertEqual(
            classify_anchor_result(12, 30, False, 0, has_structure_ocr=True),
            "needs_anchor_review",
        )

    def test_routes_number_gaps_to_review(self) -> None:
        self.assertEqual(
            classify_anchor_result(16, 59, True, 1), "needs_anchor_review"
        )


if __name__ == "__main__":
    unittest.main()
