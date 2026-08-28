import json
import tempfile
import unittest
from pathlib import Path

from exam_image_splitter.selection import SelectionError, select_manifest


class DeterministicSelectionTests(unittest.TestCase):
    def _manifest(self, root: Path) -> Path:
        manifest = {
            "version": 1,
            "build_id": "selection-test",
            "source_label": {"mode": "none"},
            "questions": [
                {
                    "id": "Q001",
                    "bundle_id": "B1",
                    "classification": {
                        "primary_category": "독서",
                        "subtype": "빈칸",
                        "unit_path": "국어/독서/인문",
                    },
                },
                {
                    "id": "Q002",
                    "bundle_id": "B1",
                    "classification": {
                        "primary_category": "독서",
                        "subtype": "내용일치",
                        "unit_path": "국어/독서/인문",
                    },
                },
                {
                    "id": "Q003",
                    "bundle_id": "B2",
                    "classification": {
                        "primary_category": "문학",
                        "subtype": "표현",
                        "unit_path": "국어/문학/현대시",
                    },
                },
                {
                    "id": "Q004",
                    "bundle_id": "B3",
                    "classification": {
                        "primary_category": "독서",
                        "subtype": "빈칸",
                        "unit_path": "국어/독서/과학",
                    },
                },
                {
                    "id": "Q005",
                    "bundle_id": "B3",
                    "classification": {
                        "primary_category": "독서",
                        "subtype": "추론",
                        "unit_path": "국어/독서/과학",
                    },
                },
                {
                    "id": "Q006",
                    "bundle_id": "B3",
                    "classification": {
                        "primary_category": "독서",
                        "subtype": "적용",
                        "unit_path": "국어/독서/과학",
                    },
                },
            ],
            "selection": [],
        }
        path = root / "bank.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return path

    def test_same_seed_and_filters_produce_the_same_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._manifest(root)
            first = select_manifest(
                source,
                root / "first.json",
                count=3,
                seed=8128,
                primary_categories=("독서",),
            )
            second = select_manifest(
                source,
                root / "second.json",
                count=3,
                seed=8128,
                primary_categories=("독서",),
            )
            first_data = json.loads(first.read_text(encoding="utf-8"))
            second_data = json.loads(second.read_text(encoding="utf-8"))
            self.assertEqual(first_data["selection"], second_data["selection"])
            self.assertEqual(first_data["selection_audit"]["seed"], 8128)
            self.assertEqual(len(first_data["selection"]), 3)
            self.assertEqual(
                [item["new_number"] for item in first_data["selection"]],
                [1, 2, 3],
            )

    def test_filters_and_exclusions_are_hard_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = select_manifest(
                self._manifest(root),
                root / "filtered.json",
                count=1,
                seed=1,
                primary_categories=("독서",),
                subtypes=("빈칸",),
                unit_prefixes=("국어/독서",),
                excluded_ids=("Q001",),
            )
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["selection"][0]["question_id"], "Q004")

    def test_whole_bundle_selection_never_selects_a_partial_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = select_manifest(
                self._manifest(root),
                root / "bundles.json",
                count=3,
                seed=17,
                bundle_policy="whole-bundle",
            )
            data = json.loads(output.read_text(encoding="utf-8"))
            selected = {item["question_id"] for item in data["selection"]}
            self.assertIn(selected, ({"Q004", "Q005", "Q006"}, {"Q001", "Q002", "Q003"}))

    def test_whole_bundle_filter_excludes_a_partially_matching_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(SelectionError, "exact whole-bundle"):
                select_manifest(
                    self._manifest(root),
                    root / "impossible.json",
                    count=2,
                    seed=17,
                    bundle_policy="whole-bundle",
                    subtypes=("빈칸",),
                )
            self.assertFalse((root / "impossible.json").exists())

    def test_exact_count_failure_does_not_write_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(SelectionError, "requested 99"):
                select_manifest(
                    self._manifest(root),
                    root / "too-many.json",
                    count=99,
                    seed=0,
                )
            self.assertFalse((root / "too-many.json").exists())


if __name__ == "__main__":
    unittest.main()
