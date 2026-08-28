import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

from exam_image_splitter.composition import (
    ANSWER_KEY_CHUNK_SIZE,
    CompositionError,
    PAPER_SIZES_MM,
    _apply_renumber,
    _answer_key_images,
    compose_exam,
    paper_profile_from_manifest,
    validate_composition_manifest,
)


FONT_PATH = Path(r"C:\Windows\Fonts\malgun.ttf")
SOURCE_NUMBER_FONT_PATH = FONT_PATH
SOURCE_TEXT_FONT_PATH = FONT_PATH


def _asset(path: Path, number: int | None, label: str, *, height: int = 760) -> None:
    image = Image.new("RGB", (1600, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_PATH), 72)
    body_font = ImageFont.truetype(str(FONT_PATH), 34)
    if number is not None:
        draw.text((25, 22), f"{number}.", font=font, fill="black")
    draw.text((240, 36), label, font=body_font, fill="black")
    for y in range(155, height - 40, 90):
        draw.line((65, y, 1500, y), fill=(45, 45, 45), width=4)
    image.save(path)
    image.close()


def _long_stimulus(path: Path) -> None:
    image = Image.new("RGB", (1600, 6600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_PATH), 36)
    draw.text((35, 20), "긴 공통 지문", font=font, fill="black")
    for block in range(20):
        top = 130 + block * 315
        for line in range(5):
            y = top + line * 42
            draw.line((70, y, 1510, y), fill=(60, 60, 60), width=4)
    image.save(path)
    image.close()


class CompositionTests(unittest.TestCase):
    def test_mask_in_place_keeps_canvas_and_anchors_long_number_leftward(self) -> None:
        image = Image.new("RGB", (500, 260), "white")
        draw = ImageDraw.Draw(image)
        source_font = ImageFont.truetype(str(SOURCE_NUMBER_FONT_PATH), 42)
        source_box = draw.textbbox((0, 0), "7.", font=source_font)
        source_x, source_y = 125, 112
        draw.text((source_x, source_y), "7.", font=source_font, fill="black")
        ink_left = source_x + source_box[0]
        ink_top = source_y + source_box[1]
        ink_right = source_x + source_box[2]
        ink_bottom = source_y + source_box[3]
        bbox = [
            (ink_left - 5) / image.width,
            (ink_top - 5) / image.height,
            (ink_right + 5) / image.width,
            (ink_bottom + 5) / image.height,
        ]

        updated = _apply_renumber(
            image,
            {
                "mode": "mask-in-place",
                "bbox": bbox,
                "number_font_path": str(SOURCE_NUMBER_FONT_PATH),
                "annotation_text": "2008년 04월 시행",
                "annotation_font_path": str(SOURCE_TEXT_FONT_PATH),
                "annotation_scale": 0.5,
            },
            replacement_text="1001.",
            font_path=FONT_PATH,
            base_dir=Path.cwd(),
            external_role="question",
        )
        try:
            self.assertEqual(updated.size, image.size)
            gray = updated.convert("L")
            try:
                # The long number must expand into the left margin while its
                # period remains at the source number's right anchor.
                self.assertLess(gray.crop((0, ink_top, ink_left, ink_bottom)).getextrema()[0], 220)
                # The small execution date is drawn in the existing top margin.
                self.assertLess(gray.crop((0, 0, ink_right, ink_top)).getextrema()[0], 220)
            finally:
                gray.close()
        finally:
            updated.close()
            image.close()

    def test_large_answer_key_is_split_into_readable_chunks(self) -> None:
        selected = [
            (
                {"question_id": f"Q{number:04d}", "new_number": number},
                {"id": f"Q{number:04d}", "answer": "①"},
            )
            for number in range(1, ANSWER_KEY_CHUNK_SIZE * 2 + 2)
        ]
        images = _answer_key_images(selected, FONT_PATH)
        try:
            self.assertEqual(len(images), 3)
            self.assertLess(images[0].height, 4000)
            self.assertLess(images[1].height, 4000)
            self.assertLess(images[2].height, 1000)
        finally:
            for image in images:
                image.close()

    def _manifest(self, root: Path, *, unsafe: bool = False) -> Path:
        _long_stimulus(root / "stimulus.png")
        _asset(root / "q3.png", 3, "첫 번째 선택 문제")
        _asset(root / "q9.png", None, "두 번째 선택 문제")
        _asset(root / "s3.png", 3, "첫 번째 선택 해설", height=920)
        _asset(root / "s9.png", None, "두 번째 선택 해설", height=920)
        manifest = {
            "version": 1,
            "build_id": "renumber-smoke",
            "title": "새 모의고사",
            "subject": "검증용",
            "source_label": {"mode": "none"},
            "font_path": str(FONT_PATH),
            "paper": {
                "size": "A4",
                "orientation": "portrait",
                "columns": 2,
                "flow_mode": "continuous-strip",
                "qa_dpi": 96,
                "min_effective_dpi": 120,
            },
            "answer": {"mode": "key-and-solutions"},
            "stimuli": [
                {
                    "id": "S1",
                    "assets": [
                        {"path": "stimulus.png", "break_policy": "flow"}
                    ],
                }
            ],
            "questions": [
                {
                    "id": "Q003",
                    "bundle_id": "B1",
                    "original_number": 3,
                    "stimulus_ids": ["S1"],
                    "assets": ["q3.png"],
                    "answer": "④",
                    "solution_assets": ["s3.png"],
                    "renumber": None if unsafe else {
                        "mode": "mask",
                        "asset_index": 0,
                        "bbox": [0.0, 0.0, 0.14, 0.16],
                    },
                    "solution_renumber": None if unsafe else {
                        "mode": "mask",
                        "asset_index": 0,
                        "bbox": [0.0, 0.0, 0.14, 0.14],
                    },
                },
                {
                    "id": "Q009",
                    "bundle_id": "B2",
                    "original_number": 9,
                    "stimulus_ids": [],
                    "assets": ["q9.png"],
                    "answer": "②",
                    "solution_assets": ["s9.png"],
                    "renumber": {"mode": "source-number-absent", "asset_index": 0},
                    "solution_renumber": {
                        "mode": "source-number-absent",
                        "asset_index": 0,
                    },
                },
            ],
            "selection": [
                {"question_id": "Q003", "new_number": 1},
                {"question_id": "Q009", "new_number": 2},
            ],
        }
        path = root / "selection.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return path

    def test_requires_safe_rule_when_number_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root, unsafe=True)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(CompositionError, "3 -> 1"):
                validate_composition_manifest(manifest, root)

    def test_requires_an_explicit_precompose_source_label_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("source_label")
            with self.assertRaisesRegex(CompositionError, "ask the user"):
                validate_composition_manifest(manifest, root)

    def test_source_label_none_rejects_a_hidden_per_question_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["questions"][0]["renumber"]["annotation_text"] = "2008년"
            with self.assertRaisesRegex(CompositionError, "mode is none"):
                validate_composition_manifest(manifest, root)

    def test_year_month_choice_requires_matching_annotation_on_every_question(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_label"] = {"mode": "year-month"}
            manifest["answer"] = {"mode": "key-only"}
            manifest["questions"] = [manifest["questions"][0]]
            manifest["selection"] = [{"question_id": "Q003", "new_number": 1}]
            manifest["questions"][0]["renumber"] = {
                "mode": "mask-in-place",
                "asset_index": 0,
                "bbox": [0.0, 0.0, 0.14, 0.16],
                "annotation_text": "2008년 04월 시행",
            }
            context = validate_composition_manifest(manifest, root)
            self.assertEqual(context["source_label_mode"], "year-month")

    def test_accepts_contiguous_chunk_numbering_from_declared_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["numbering_start"] = 101
            manifest["selection"] = [
                {"question_id": "Q003", "new_number": 101},
                {"question_id": "Q009", "new_number": 102},
            ]
            context = validate_composition_manifest(manifest, root)
            self.assertEqual(
                [item[0]["new_number"] for item in context["selected"]],
                [101, 102],
            )

    def test_mask_and_prepend_supports_long_global_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["numbering_start"] = 1001
            manifest["selection"] = [
                {"question_id": "Q003", "new_number": 1001},
                {"question_id": "Q009", "new_number": 1002},
            ]
            for question in manifest["questions"]:
                question["renumber"] = {
                    "mode": "mask-and-prepend",
                    "asset_index": 0,
                    "bbox": [0.0, 0.0, 0.14, 0.16],
                }
                question["solution_renumber"] = {
                    "mode": "mask-and-prepend",
                    "asset_index": 0,
                    "bbox": [0.0, 0.0, 0.14, 0.14],
                }
            context = validate_composition_manifest(manifest, root)
            self.assertEqual(len(context["selected"]), 2)

    def test_composes_synced_problem_and_answer_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            report_path = compose_exam(manifest_path, root / "output")
            report = json.loads(report_path.read_text(encoding="utf-8"))

            self.assertEqual(report["status"], "complete")
            self.assertEqual(
                [(row["original_number"], row["new_number"]) for row in report["numbering_map"]],
                [(3, 1), (9, 2)],
            )
            self.assertEqual(len(report["renumber_actions"]), 4)
            self.assertTrue(report["checks"]["problem_answer_numbering_synchronized"])
            self.assertTrue(report["checks"]["page_sizes_match"])
            self.assertEqual(report["checks"]["out_of_bounds_placements"], 0)
            self.assertTrue(report["checks"]["source_pixel_ranges_contiguous"])
            self.assertEqual(report["checks"]["protected_range_splits"], 0)
            self.assertEqual(report["paper"]["flow_mode"], "continuous-strip")
            for key in ("problem", "answer"):
                pdf_path = Path(report[key]["pdf"])
                self.assertTrue(pdf_path.is_file())
                document = pdfium.PdfDocument(str(pdf_path))
                try:
                    self.assertGreaterEqual(len(document), 1)
                    page = document[0]
                    try:
                        width, height = page.get_size()
                    finally:
                        page.close()
                    self.assertAlmostEqual(width / 72 * 25.4, 210, delta=0.4)
                    self.assertAlmostEqual(height / 72 * 25.4, 297, delta=0.4)
                finally:
                    document.close()
            problem_qa = list((report_path.parent / "qa" / "problems").glob("*.png"))
            answer_qa = list((report_path.parent / "qa" / "answers").glob("*.png"))
            self.assertEqual(len(problem_qa), report["problem"]["page_count"])
            self.assertEqual(len(answer_qa), report["answer"]["page_count"])
            self.assertGreaterEqual(
                len({row["column"] for row in report["problem"]["placements"]}),
                2,
            )

    def test_existing_build_requires_explicit_overwrite_and_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            output = root / "output"
            first = compose_exam(manifest_path, output, render_qa=False)
            self.assertTrue(first.is_file())
            with self.assertRaisesRegex(CompositionError, "--overwrite"):
                compose_exam(manifest_path, output, render_qa=False)
            replacement = compose_exam(
                manifest_path, output, render_qa=False, overwrite=True
            )
            self.assertTrue(replacement.is_file())
            self.assertEqual(list(output.glob(".renumber-smoke.*")), [])

    def test_failed_verification_leaves_no_partial_final_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            output = root / "output"
            with patch(
                "exam_image_splitter.composition._verify_and_render_pdf",
                side_effect=CompositionError("simulated QA failure"),
            ):
                with self.assertRaisesRegex(CompositionError, "simulated QA failure"):
                    compose_exam(manifest_path, output, render_qa=False)
            self.assertFalse((output / "renumber-smoke").exists())
            self.assertEqual(list(output.glob(".renumber-smoke.*")), [])

    def test_noncontiguous_bundle_runs_get_truthful_single_question_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._manifest(root)
            _asset(root / "q10.png", None, "세 번째 선택 문제")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["build_id"] = "bundle-runs"
            manifest["answer"] = {"mode": "key-only"}
            manifest["stimuli"][0]["range_renumber"] = {
                "mode": "mask",
                "asset_index": 0,
                "bbox": [0.0, 0.0, 0.25, 0.04],
            }
            manifest["questions"].append(
                {
                    "id": "Q010",
                    "bundle_id": "B1",
                    "original_number": 10,
                    "stimulus_ids": ["S1"],
                    "assets": ["q10.png"],
                    "answer": "①",
                    "renumber": {"mode": "source-number-absent", "asset_index": 0},
                }
            )
            manifest["selection"] = [
                {"question_id": "Q003", "new_number": 1},
                {"question_id": "Q009", "new_number": 2},
                {"question_id": "Q010", "new_number": 3},
            ]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            report_path = compose_exam(manifest_path, root / "output", render_qa=False)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            labels = [
                action["replacement_text"]
                for action in report["renumber_actions"]
                if action["role"] == "stimulus"
            ]
            self.assertEqual(labels, ["[1]", "[3]"])

    def _flow_manifest(self, root: Path, break_policy: str) -> Path:
        _asset(root / "q1.png", 1, "앞 문제", height=3100)
        _asset(root / "q2.png", 2, "경계에서 이어지는 문제", height=3000)
        manifest = {
            "version": 1,
            "build_id": f"flow-{break_policy}",
            "title": "연속 흐름 검증",
            "source_label": {"mode": "none"},
            "font_path": str(FONT_PATH),
            "paper": {
                "size": "A4",
                "columns": 2,
                "flow_mode": "continuous-strip",
                "qa_dpi": 96,
                "min_effective_dpi": 120,
            },
            "answer": {"mode": "key-only"},
            "stimuli": [],
            "questions": [
                {
                    "id": "Q001",
                    "original_number": 1,
                    "assets": [{"path": "q1.png", "break_policy": "flow"}],
                    "stimulus_ids": [],
                    "answer": "①",
                },
                {
                    "id": "Q002",
                    "original_number": 2,
                    "assets": [
                        {"path": "q2.png", "break_policy": break_policy}
                    ],
                    "stimulus_ids": [],
                    "answer": "②",
                },
            ],
            "selection": [
                {"question_id": "Q001", "new_number": 1},
                {"question_id": "Q002", "new_number": 2},
            ],
        }
        path = root / f"selection-{break_policy}.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return path

    def test_flow_asset_is_split_at_the_current_column_seam(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_path = compose_exam(
                self._flow_manifest(root, "flow"), root / "output", render_qa=False
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIsNone(report["checks"]["blank_pages"])
            self.assertFalse(report["checks"]["blank_pages_checked"])
            placements = [
                row
                for row in report["problem"]["placements"]
                if row["question_id"] == "Q002"
            ]
            self.assertEqual(len(placements), 2)
            self.assertEqual(
                [(row["page"], row["column"]) for row in placements],
                [(1, 1), (1, 2)],
            )
            self.assertEqual(placements[0]["source_pixel_y"][0], 0)
            self.assertEqual(
                placements[0]["source_pixel_y"][1],
                placements[1]["source_pixel_y"][0],
            )
            self.assertEqual(placements[1]["source_pixel_y"][1], 3000)
            seams = [
                event
                for event in report["problem"]["seam_events"]
                if event["asset_id"] == "Q002__01"
            ]
            self.assertEqual(seams[0]["kind"], "split")
            self.assertEqual(seams[0]["method"], "detected-whitespace")

    def test_flow_continues_from_right_column_to_next_page_left(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._flow_manifest(root, "flow")
            _asset(root / "q2.png", 2, "여러 단에 이어지는 문제", height=7200)
            report_path = compose_exam(
                manifest_path, root / "output", render_qa=False
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            placements = [
                row
                for row in report["problem"]["placements"]
                if row["question_id"] == "Q002"
            ]
            self.assertEqual(
                [(row["page"], row["column"]) for row in placements[:3]],
                [(1, 1), (1, 2), (2, 1)],
            )
            ranges = [row["source_pixel_y"] for row in placements]
            self.assertEqual(ranges[0][0], 0)
            for previous, current in zip(ranges, ranges[1:]):
                self.assertEqual(previous[1], current[0])
            self.assertEqual(ranges[-1][1], 7200)

    def test_keep_together_asset_moves_whole_to_the_next_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_path = compose_exam(
                self._flow_manifest(root, "keep-together"),
                root / "output",
                render_qa=False,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            placements = [
                row
                for row in report["problem"]["placements"]
                if row["question_id"] == "Q002"
            ]
            self.assertEqual(len(placements), 1)
            self.assertEqual((placements[0]["page"], placements[0]["column"]), (1, 2))
            seams = [
                event
                for event in report["problem"]["seam_events"]
                if event["asset_id"] == "Q002__01"
            ]
            self.assertEqual(seams[0]["kind"], "padding")
            self.assertEqual(seams[0]["method"], "keep-together")

    def test_protected_range_is_not_cut_at_the_column_seam(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = self._flow_manifest(root, "flow")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["build_id"] = "flow-protected"
            manifest["questions"][1]["assets"][0]["protected_ranges"] = [
                [0.0, 0.60]
            ]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            report_path = compose_exam(
                manifest_path, root / "output", render_qa=False
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            placements = [
                row
                for row in report["problem"]["placements"]
                if row["question_id"] == "Q002"
            ]
            self.assertEqual(len(placements), 1)
            self.assertEqual((placements[0]["page"], placements[0]["column"]), (1, 2))
            self.assertEqual(report["checks"]["protected_range_splits"], 0)

    def test_all_supported_paper_profiles_have_exact_dimensions(self) -> None:
        for name, dimensions in PAPER_SIZES_MM.items():
            with self.subTest(name=name):
                profile = paper_profile_from_manifest({"size": name})
                self.assertAlmostEqual(profile.width_pt / 72 * 25.4, dimensions[0])
                self.assertAlmostEqual(profile.height_pt / 72 * 25.4, dimensions[1])

    def test_composes_actual_supported_paper_media_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_manifest_path = self._manifest(root)
            base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
            for name, dimensions in PAPER_SIZES_MM.items():
                with self.subTest(name=name):
                    manifest = dict(base_manifest)
                    manifest["build_id"] = f"paper-{name.lower()}"
                    manifest["paper"] = dict(base_manifest["paper"], size=name)
                    manifest_path = root / f"selection-{name}.json"
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
                    )
                    report_path = compose_exam(
                        manifest_path, root / "paper-output", render_qa=False
                    )
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    for key in ("problem", "answer"):
                        document = pdfium.PdfDocument(report[key]["pdf"])
                        try:
                            page = document[0]
                            try:
                                width, height = page.get_size()
                            finally:
                                page.close()
                        finally:
                            document.close()
                        self.assertAlmostEqual(
                            width / 72 * 25.4, dimensions[0], delta=0.4
                        )
                        self.assertAlmostEqual(
                            height / 72 * 25.4, dimensions[1], delta=0.4
                        )

    def test_korean_eighth_sheet_aliases_are_unambiguous(self) -> None:
        common = paper_profile_from_manifest({"size": "8절지"})
        gukjeon = paper_profile_from_manifest({"size": "국전8절"})
        self.assertEqual(common.name, "8JEOL")
        self.assertEqual(gukjeon.name, "GUKJEON-8JEOL")
        self.assertAlmostEqual(common.width_pt / 72 * 25.4, 272.0)
        self.assertAlmostEqual(common.height_pt / 72 * 25.4, 394.0)
        self.assertAlmostEqual(gukjeon.width_pt / 72 * 25.4, 234.0)
        self.assertAlmostEqual(gukjeon.height_pt / 72 * 25.4, 318.0)

    def test_eighth_sheet_landscape_swaps_exact_dimensions(self) -> None:
        profile = paper_profile_from_manifest(
            {"size": "8JEOL", "orientation": "landscape"}
        )
        self.assertAlmostEqual(profile.width_pt / 72 * 25.4, 394.0)
        self.assertAlmostEqual(profile.height_pt / 72 * 25.4, 272.0)


if __name__ == "__main__":
    unittest.main()
