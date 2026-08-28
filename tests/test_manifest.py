import copy
import unittest

from exam_image_splitter.manifest import ManifestError, validate_manifest


def valid_manifest() -> dict:
    return {
        "version": 1,
        "exam_id": "sample",
        "source_pdf": "sample.pdf",
        "render": {"dpi": 72, "output_width_px": 600, "padding_px": 20},
        "fragments": [
            {
                "id": "s1",
                "kind": "stimulus",
                "page": 1,
                "bbox": [0.1, 0.1, 0.4, 0.4],
            },
            {
                "id": "q1",
                "kind": "question",
                "page": 2,
                "bbox": [0.5, 0.2, 0.9, 0.8],
            },
        ],
        "stimuli": [{"id": "S1", "fragment_ids": ["s1"]}],
        "questions": [
            {
                "id": "Q1",
                "section": "common",
                "number": 1,
                "stimulus_ids": ["S1"],
                "fragment_ids": ["q1"],
            }
        ],
    }


class ManifestTests(unittest.TestCase):
    def test_valid_manifest(self) -> None:
        validate_manifest(valid_manifest(), page_count=2)

    def test_rejects_out_of_range_page_and_bbox(self) -> None:
        manifest = valid_manifest()
        manifest["fragments"][0]["page"] = 3
        manifest["fragments"][1]["bbox"] = [0.8, 0.2, 0.7, 0.9]
        with self.assertRaises(ManifestError) as context:
            validate_manifest(manifest, page_count=2)
        message = str(context.exception)
        self.assertIn("exceeds PDF page count", message)
        self.assertIn("bbox must satisfy", message)

    def test_rejects_cross_kind_reference(self) -> None:
        manifest = valid_manifest()
        manifest["stimuli"][0]["fragment_ids"] = ["q1"]
        with self.assertRaisesRegex(ManifestError, "non-stimulus"):
            validate_manifest(manifest)

    def test_rejects_duplicate_export_filename(self) -> None:
        manifest = valid_manifest()
        second = copy.deepcopy(manifest["questions"][0])
        second["id"] = "Q2"
        second["number"] = 2
        second["export"] = {"filename": "Q1.png"}
        manifest["questions"].append(second)
        with self.assertRaisesRegex(ManifestError, "duplicate export filename"):
            validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()

