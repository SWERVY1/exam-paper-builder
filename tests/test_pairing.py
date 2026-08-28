import tempfile
import unittest
from pathlib import Path

from exam_image_splitter.pairing import normalized_exam_stem, pair_exam_directories


class PairingTests(unittest.TestCase):
    def test_normalizes_only_the_final_solution_suffix(self) -> None:
        self.assertEqual(normalized_exam_stem("2025 국어 해설.pdf"), "2025 국어")
        self.assertEqual(normalized_exam_stem("해설 연습 문제.pdf"), "해설 연습 문제")

    def test_reports_pairs_unmatched_and_ambiguous_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            problems = root / "problems"
            solutions = root / "solutions"
            problems.mkdir()
            solutions.mkdir()
            (problems / "시험A.pdf").touch()
            (solutions / "시험A 해설.pdf").touch()
            (problems / "시험B.pdf").touch()
            (solutions / "시험C 해설.pdf").touch()
            (problems / "nested1").mkdir()
            (problems / "nested2").mkdir()
            (problems / "nested1" / "중복.pdf").touch()
            (problems / "nested2" / "중복.pdf").touch()

            report = pair_exam_directories(problems, solutions)

            self.assertEqual(report["pair_count"], 1)
            self.assertEqual(report["problem_only_count"], 1)
            self.assertEqual(report["solution_only_count"], 1)
            self.assertEqual(report["ambiguous_count"], 1)
            self.assertEqual(report["pairs"][0]["pair_id"], "시험A")


if __name__ == "__main__":
    unittest.main()
