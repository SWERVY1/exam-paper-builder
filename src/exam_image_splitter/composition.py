"""Compose selected exam image assets into verified problem/answer PDFs."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pypdfium2 as pdfium
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


MM_TO_POINTS = 72.0 / 25.4
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "B4-JIS": (257.0, 364.0),
    "B4-ISO": (250.0, 353.0),
    # Korean paper names are not ISO names.  Keep the common 4x6 8-jeol and
    # the smaller gukjeon 8-jeol explicit so a print job cannot silently use
    # the wrong cut size.
    "8JEOL": (272.0, 394.0),
    "GUKJEON-8JEOL": (234.0, 318.0),
}
ANSWER_MODES = {"key-only", "solutions-only", "key-and-solutions"}
FLOW_MODES = {"continuous-strip"}
BREAK_POLICIES = {"flow", "safe-only", "keep-together"}
SOURCE_LABEL_MODES = {"none", "year", "year-month"}
SOURCE_YEAR_PATTERN = re.compile(r"^\d{4}년$")
SOURCE_YEAR_MONTH_PATTERN = re.compile(r"^\d{4}년 (?:0[1-9]|1[0-2])월 시행$")
ANSWER_KEY_CHUNK_SIZE = 200


class CompositionError(ValueError):
    """Raised when a requested composition cannot be completed safely."""


@dataclass(frozen=True)
class PaperProfile:
    name: str
    width_pt: float
    height_pt: float
    orientation: str
    columns: int
    margin_top_pt: float
    margin_right_pt: float
    margin_bottom_pt: float
    margin_left_pt: float
    gutter_pt: float
    header_pt: float
    footer_pt: float
    gap_pt: float
    min_effective_dpi: float
    min_scale_fraction: float
    qa_dpi: int
    flow_mode: str

    @property
    def content_top(self) -> float:
        return self.height_pt - self.margin_top_pt - self.header_pt

    @property
    def content_bottom(self) -> float:
        return self.margin_bottom_pt + self.footer_pt

    @property
    def usable_height(self) -> float:
        return self.content_top - self.content_bottom

    @property
    def column_width(self) -> float:
        available = self.width_pt - self.margin_left_pt - self.margin_right_pt
        return (available - self.gutter_pt * (self.columns - 1)) / self.columns

    def column_x(self, column: int) -> float:
        return self.margin_left_pt + column * (self.column_width + self.gutter_pt)


@dataclass
class FlowAsset:
    asset_id: str
    role: str
    source_path: str
    image: Image.Image
    break_policy: str = "flow"
    safe_breaks: tuple[float, ...] = ()
    protected_ranges: tuple[tuple[float, float], ...] = ()
    keep_with_next: bool = False
    min_scale_fraction: float | None = None
    question_id: str | None = None
    bundle_id: str | None = None
    new_number: int | None = None
    original_number: int | None = None


@dataclass
class Placement:
    asset_id: str
    role: str
    source_path: str
    page: int
    column: int
    bbox_points: tuple[float, float, float, float]
    pixel_size: tuple[int, int]
    effective_dpi: float
    scale_fraction: float
    part: int = 1
    question_id: str | None = None
    bundle_id: str | None = None
    new_number: int | None = None
    original_number: int | None = None
    source_pixel_y: tuple[int, int] | None = None
    source_pixel_height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "role": self.role,
            "source_path": self.source_path,
            "page": self.page,
            "column": self.column + 1,
            "bbox_points": [round(value, 3) for value in self.bbox_points],
            "pixel_size": list(self.pixel_size),
            "effective_dpi": round(self.effective_dpi, 2),
            "scale_fraction": round(self.scale_fraction, 4),
            "part": self.part,
            "question_id": self.question_id,
            "bundle_id": self.bundle_id,
            "new_number": self.new_number,
            "original_number": self.original_number,
            "source_pixel_y": (
                list(self.source_pixel_y) if self.source_pixel_y is not None else None
            ),
            "source_pixel_height": self.source_pixel_height,
        }


def compose_exam(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    render_qa: bool = True,
    overwrite: bool = False,
) -> Path:
    """Create exactly one problem PDF and one answer PDF from a selection.

    The complete build is written to a private sibling directory and exposed
    under ``output_root/build_id`` only after every requested check passes.
    Existing builds are never replaced unless ``overwrite`` is explicit.
    """

    source_manifest = Path(manifest_path).resolve()
    if not source_manifest.is_file():
        raise FileNotFoundError(source_manifest)
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    context = validate_composition_manifest(manifest, source_manifest.parent)
    profile: PaperProfile = context["profile"]
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = context["selected"]
    font_path: Path = context["font_path"]
    font_name = _register_pdf_font(font_path)

    build_id = _safe_build_id(str(manifest["build_id"]))
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / build_id
    if destination.exists() and not overwrite:
        raise CompositionError(
            f"Output build already exists: {destination}. Use --overwrite explicitly."
        )

    token = uuid.uuid4().hex
    staging = output / f".{build_id}.tmp-{token}"
    staging.mkdir()
    _require_direct_child(staging, output)
    backup: Path | None = None
    final_problem_pdf: Path | None = None
    final_answer_pdf: Path | None = None
    try:
        qa_root = staging / "qa"
        if render_qa:
            (qa_root / "problems").mkdir(parents=True)
            (qa_root / "answers").mkdir(parents=True)

        paper_token = profile.name.replace("-", "_").lower()
        problem_name = f"{build_id}_problems_{paper_token}.pdf"
        answer_name = f"{build_id}_answers_{paper_token}.pdf"
        problem_pdf = staging / problem_name
        answer_pdf = staging / answer_name
        final_problem_pdf = destination / problem_name
        final_answer_pdf = destination / answer_name
        problem_tmp = problem_pdf.with_suffix(".tmp.pdf")
        answer_tmp = answer_pdf.with_suffix(".tmp.pdf")

        renumber_actions: list[dict[str, Any]] = []
        problem_assets = _problem_flow_assets(
            manifest,
            selected,
            source_manifest.parent,
            font_path,
            renumber_actions,
        )
        answer_assets = _answer_flow_assets(
            manifest,
            selected,
            source_manifest.parent,
            font_path,
            renumber_actions,
        )

        title = str(manifest.get("title") or build_id)
        subject = str(manifest.get("subject") or "")
        problem_writer = _FlowPdfWriter(
            problem_tmp,
            profile,
            title=title,
            subtitle=subject,
            font_name=font_name,
            page_number_start=int(manifest.get("problem_page_number_start", 1)),
        )
        answer_writer = _FlowPdfWriter(
            answer_tmp,
            profile,
            title=f"{title} - 정답 및 해설",
            subtitle=subject,
            font_name=font_name,
            page_number_start=int(manifest.get("answer_page_number_start", 1)),
        )
        problem_placements = problem_writer.write(problem_assets)
        answer_placements = answer_writer.write(answer_assets)
        os.replace(problem_tmp, problem_pdf)
        os.replace(answer_tmp, answer_pdf)

        problem_check = _verify_and_render_pdf(
            problem_pdf,
            profile,
            qa_root / "problems" if render_qa else None,
        )
        answer_check = _verify_and_render_pdf(
            answer_pdf,
            profile,
            qa_root / "answers" if render_qa else None,
        )

        snapshot = _materialize_manifest_paths(manifest, source_manifest.parent)
        snapshot["source_manifest"] = str(source_manifest)
        (staging / "selection-manifest.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        numbering_map = [
            {
                "question_id": question["id"],
                "original_number": question["original_number"],
                "new_number": selection["new_number"],
                "answer": question.get("answer"),
            }
            for selection, question in selected
        ]
        expected_numbers = [int(item["new_number"]) for item, _question in selected]
        question_ids = [str(question["id"]) for _item, question in selected]
        problem_numbers = _ordered_unique_numbers(problem_placements, "question")
        solution_numbers = _ordered_unique_numbers(answer_placements, "solution")
        answer_mode = context["answer_mode"]
        answer_key_expected = answer_mode in {"key-only", "key-and-solutions"}
        answer_key_present = any(
            item.role == "answer_key" for item in answer_placements
        )
        numbering_synchronized = problem_numbers == expected_numbers
        if answer_mode in {"solutions-only", "key-and-solutions"}:
            numbering_synchronized = (
                numbering_synchronized and solution_numbers == expected_numbers
            )
        if answer_key_expected:
            numbering_synchronized = numbering_synchronized and answer_key_present

        out_of_bounds = _count_out_of_bounds(
            [*problem_placements, *answer_placements], profile
        )
        protected_splits = _count_protected_splits(
            [*problem_writer.seam_events, *answer_writer.seam_events]
        )
        pixel_ranges_contiguous = _source_ranges_contiguous(
            [*problem_placements, *answer_placements]
        )
        blank_pages = (
            int(problem_check["blank_pages"]) + int(answer_check["blank_pages"])
            if problem_check["blank_pages"] is not None
            and answer_check["blank_pages"] is not None
            else None
        )
        checks = {
            "question_ids_unique": len(question_ids) == len(set(question_ids)),
            "new_numbers_contiguous": expected_numbers
            == list(
                range(
                    int(manifest.get("numbering_start", 1)),
                    int(manifest.get("numbering_start", 1)) + len(selected),
                )
            ),
            "problem_answer_numbering_synchronized": numbering_synchronized,
            "problem_pdf_opened": problem_check["opened"],
            "answer_pdf_opened": answer_check["opened"],
            "page_sizes_match": bool(problem_check["page_sizes_match"])
            and bool(answer_check["page_sizes_match"]),
            "out_of_bounds_placements": out_of_bounds,
            "blank_pages": blank_pages,
            "blank_pages_checked": bool(problem_check["blank_pages_checked"])
            and bool(answer_check["blank_pages_checked"]),
            "source_pixel_ranges_contiguous": pixel_ranges_contiguous,
            "protected_range_splits": protected_splits,
            "continuous_strip_flow": profile.flow_mode == "continuous-strip",
            "qa_rendered": render_qa,
        }
        hard_failures = [
            key
            for key in (
                "question_ids_unique",
                "new_numbers_contiguous",
                "problem_answer_numbering_synchronized",
                "problem_pdf_opened",
                "answer_pdf_opened",
                "page_sizes_match",
                "source_pixel_ranges_contiguous",
                "continuous_strip_flow",
            )
            if checks[key] is not True
        ]
        if out_of_bounds:
            hard_failures.append("out_of_bounds_placements")
        if protected_splits:
            hard_failures.append("protected_range_splits")
        if blank_pages not in (None, 0):
            hard_failures.append("blank_pages")
        if hard_failures:
            raise CompositionError(
                "Composition checks failed: " + ", ".join(hard_failures)
            )

        report = {
            "schema_version": 2,
            "status": "complete",
            "build_id": build_id,
            "source_manifest": str(source_manifest),
            "source_label": {"mode": context["source_label_mode"]},
            "paper": _profile_dict(profile),
            "selection_count": len(selected),
            "numbering_map": numbering_map,
            "renumber_actions": renumber_actions,
            "problem": {
                "pdf": str(final_problem_pdf),
                "page_count": problem_check["page_count"],
                "placements": [item.as_dict() for item in problem_placements],
                "seam_events": problem_writer.seam_events,
            },
            "answer": {
                "pdf": str(final_answer_pdf),
                "page_count": answer_check["page_count"],
                "mode": answer_mode,
                "placements": [item.as_dict() for item in answer_placements],
                "seam_events": answer_writer.seam_events,
            },
            "checks": checks,
        }
        (staging / "composition-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if destination.exists():
            backup = output / f".{build_id}.backup-{token}"
            _require_direct_child(backup, output)
            os.replace(destination, backup)
        os.replace(staging, destination)
        if backup is not None:
            _remove_internal_path(backup, output)
            backup = None
        return destination / "composition-report.json"
    except Exception:
        if staging.exists():
            _remove_internal_path(staging, output)
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
            backup = None
        raise


def validate_composition_manifest(
    manifest: dict[str, Any],
    base_dir: str | Path,
) -> dict[str, Any]:
    """Validate selection, numbering, assets, paper, and answer invariants."""

    if not isinstance(manifest, dict):
        raise CompositionError("Composition manifest must be a JSON object")
    if manifest.get("version") != 1:
        raise CompositionError("Composition manifest version must be 1")
    if not str(manifest.get("build_id", "")).strip():
        raise CompositionError("build_id is required")
    source_label_mode = _validate_source_label_choice(manifest.get("source_label"))

    profile = paper_profile_from_manifest(manifest.get("paper", {}))
    answer = manifest.get("answer", {})
    if not isinstance(answer, dict):
        raise CompositionError("answer must be an object")
    answer_mode = answer.get("mode", "key-and-solutions")
    if answer_mode not in ANSWER_MODES:
        raise CompositionError(
            f"answer.mode must be one of {sorted(ANSWER_MODES)}"
        )

    questions = manifest.get("questions")
    selection = manifest.get("selection")
    stimuli = manifest.get("stimuli", [])
    if not isinstance(questions, list) or not questions:
        raise CompositionError("questions must be a non-empty array")
    if not isinstance(selection, list) or not selection:
        raise CompositionError("selection must be a non-empty array")
    if not isinstance(stimuli, list):
        raise CompositionError("stimuli must be an array")

    question_index = _unique_index(questions, "questions")
    stimulus_index = _unique_index(stimuli, "stimuli")
    selected_ids = [str(item.get("question_id", "")) for item in selection]
    if any(not item for item in selected_ids):
        raise CompositionError("Every selection requires question_id")
    if len(selected_ids) != len(set(selected_ids)):
        raise CompositionError("selection contains duplicate question_id values")
    numbering_start = int(manifest.get("numbering_start", 1))
    if numbering_start <= 0:
        raise CompositionError("numbering_start must be a positive integer")
    new_numbers = [item.get("new_number") for item in selection]
    expected_numbers = list(
        range(numbering_start, numbering_start + len(selection))
    )
    if new_numbers != expected_numbers:
        raise CompositionError(
            "selection new_number values must be contiguous from numbering_start"
        )

    root = Path(base_dir).resolve()
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for selection_item in selection:
        question_id = str(selection_item["question_id"])
        if question_id not in question_index:
            raise CompositionError(f"Unknown selected question: {question_id}")
        question = question_index[question_id]
        original_number = question.get("original_number")
        if not isinstance(original_number, int) or original_number <= 0:
            raise CompositionError(
                f"Question {question_id} requires positive integer original_number"
            )
        if not isinstance(question.get("assets"), list) or not question["assets"]:
            raise CompositionError(f"Question {question_id} requires assets")
        for stimulus_id in question.get("stimulus_ids", []):
            if stimulus_id not in stimulus_index:
                raise CompositionError(
                    f"Question {question_id} references unknown stimulus {stimulus_id}"
                )

        new_number = int(selection_item["new_number"])
        _validate_renumber(
            question.get("renumber"),
            original_number=original_number,
            new_number=new_number,
            asset_count=len(question["assets"]),
            label=f"Question {question_id}",
        )
        _validate_question_source_label(
            question.get("renumber"),
            mode=source_label_mode,
            label=f"Question {question_id}",
        )
        if answer_mode in {"key-only", "key-and-solutions"}:
            if not str(question.get("answer", "")).strip():
                raise CompositionError(f"Question {question_id} has no answer")
        if answer_mode in {"solutions-only", "key-and-solutions"}:
            solution_assets = question.get("solution_assets")
            if not isinstance(solution_assets, list) or not solution_assets:
                raise CompositionError(
                    f"Question {question_id} has no solution_assets"
                )
            _validate_renumber(
                question.get("solution_renumber"),
                original_number=original_number,
                new_number=new_number,
                asset_count=len(solution_assets),
                label=f"Solution {question_id}",
            )
        selected.append((selection_item, question))

    referenced_stimuli = {
        stimulus_id
        for _selection_item, question in selected
        for stimulus_id in question.get("stimulus_ids", [])
    }
    for stimulus_id in referenced_stimuli:
        stimulus = stimulus_index[stimulus_id]
        if not isinstance(stimulus.get("assets"), list) or not stimulus["assets"]:
            raise CompositionError(f"Stimulus {stimulus_id} requires assets")
        range_spec = stimulus.get("range_renumber")
        if range_spec is not None:
            _validate_replacement_spec(
                range_spec,
                asset_count=len(stimulus["assets"]),
                label=f"Stimulus {stimulus_id} range_renumber",
                allow_preserve=False,
            )

    required_items = [question for _selection, question in selected]
    required_items.extend(stimulus_index[item] for item in referenced_stimuli)
    for item in required_items:
        for field_name in ("assets", "solution_assets"):
            for asset in item.get(field_name, []) or []:
                _validate_asset_spec(asset)
                path = _asset_path(asset, root)
                if not path.is_file():
                    raise FileNotFoundError(path)
                try:
                    with Image.open(path) as image:
                        image.verify()
                except Exception as exc:
                    raise CompositionError(f"Unreadable image asset: {path}") from exc

    font_path = _font_path(manifest, root)
    return {
        "profile": profile,
        "answer_mode": answer_mode,
        "source_label_mode": source_label_mode,
        "selected": selected,
        "question_index": question_index,
        "stimulus_index": stimulus_index,
        "font_path": font_path,
    }


def paper_profile_from_manifest(value: Any) -> PaperProfile:
    if not isinstance(value, dict):
        raise CompositionError("paper must be an object")
    raw_name = str(value.get("size", "B4-JIS")).strip()
    name = raw_name.upper()
    canonical = {
        "B4": "B4-JIS",
        "B4_JIS": "B4-JIS",
        "B4_ISO": "B4-ISO",
        "8절": "8JEOL",
        "8절지": "8JEOL",
        "4X6-8JEOL": "8JEOL",
        "4X6_8JEOL": "8JEOL",
        "4X6 8JEOL": "8JEOL",
        "국8절": "GUKJEON-8JEOL",
        "국전8절": "GUKJEON-8JEOL",
        "GUKJEON_8JEOL": "GUKJEON-8JEOL",
        "GUKJEON 8JEOL": "GUKJEON-8JEOL",
    }.get(name, name)
    if canonical not in PAPER_SIZES_MM:
        raise CompositionError(f"Unsupported paper size: {raw_name}")
    orientation = str(value.get("orientation", "portrait")).lower()
    if orientation not in {"portrait", "landscape"}:
        raise CompositionError("paper.orientation must be portrait or landscape")
    width_mm, height_mm = PAPER_SIZES_MM[canonical]
    if orientation == "landscape":
        width_mm, height_mm = height_mm, width_mm
    columns = int(value.get("columns", 2))
    if columns not in {1, 2, 3}:
        raise CompositionError("paper.columns must be 1, 2, or 3")
    margins = value.get("margins_mm", {})
    if not isinstance(margins, dict):
        raise CompositionError("paper.margins_mm must be an object")

    def positive(name_: str, default: float, *, allow_zero: bool = False) -> float:
        number = float(value.get(name_, default))
        if number < 0 or (not allow_zero and number == 0):
            raise CompositionError(f"paper.{name_} must be positive")
        return number

    margin_values = {
        side: float(margins.get(side, 12.0)) for side in ("top", "right", "bottom", "left")
    }
    if any(number < 0 for number in margin_values.values()):
        raise CompositionError("paper margins cannot be negative")
    flow_mode = str(value.get("flow_mode", "continuous-strip"))
    if flow_mode not in FLOW_MODES:
        raise CompositionError(
            f"paper.flow_mode must be one of {sorted(FLOW_MODES)}"
        )
    profile = PaperProfile(
        name=canonical,
        width_pt=width_mm * MM_TO_POINTS,
        height_pt=height_mm * MM_TO_POINTS,
        orientation=orientation,
        columns=columns,
        margin_top_pt=margin_values["top"] * MM_TO_POINTS,
        margin_right_pt=margin_values["right"] * MM_TO_POINTS,
        margin_bottom_pt=margin_values["bottom"] * MM_TO_POINTS,
        margin_left_pt=margin_values["left"] * MM_TO_POINTS,
        gutter_pt=positive("gutter_mm", 8.0, allow_zero=True) * MM_TO_POINTS,
        header_pt=positive("header_mm", 12.0, allow_zero=True) * MM_TO_POINTS,
        footer_pt=positive("footer_mm", 7.0, allow_zero=True) * MM_TO_POINTS,
        gap_pt=positive("asset_gap_mm", 3.0, allow_zero=True) * MM_TO_POINTS,
        min_effective_dpi=positive("min_effective_dpi", 150.0),
        min_scale_fraction=float(value.get("min_scale_fraction", 0.72)),
        qa_dpi=int(value.get("qa_dpi", 120)),
        flow_mode=flow_mode,
    )
    if not 0.25 <= profile.min_scale_fraction <= 1.0:
        raise CompositionError("paper.min_scale_fraction must be inside 0.25..1")
    if not 72 <= profile.qa_dpi <= 300:
        raise CompositionError("paper.qa_dpi must be inside 72..300")
    if profile.column_width <= 0 or profile.usable_height <= 0:
        raise CompositionError("Paper margins, header, footer, and gutter leave no content area")
    return profile


class _FlowPdfWriter:
    def __init__(
        self,
        output_path: Path,
        profile: PaperProfile,
        *,
        title: str,
        subtitle: str,
        font_name: str,
        page_number_start: int = 1,
    ) -> None:
        self.output_path = output_path
        self.profile = profile
        self.title = title
        self.subtitle = subtitle
        self.font_name = font_name
        self.pdf = canvas.Canvas(
            str(output_path), pagesize=(profile.width_pt, profile.height_pt)
        )
        if page_number_start <= 0:
            raise CompositionError("page_number_start must be positive")
        self.page = page_number_start
        self.column = 0
        self.cursor_y = profile.content_top
        self.page_has_content = False
        self.placements: list[Placement] = []
        self.seam_events: list[dict[str, Any]] = []
        self._draw_header()

    def write(self, assets: Iterable[FlowAsset]) -> list[Placement]:
        """Write a lazy asset stream while holding at most two bitmaps in memory."""

        iterator = iter(assets)
        try:
            current = next(iterator)
        except StopIteration as exc:
            raise CompositionError("A PDF flow cannot be empty") from exc

        following: FlowAsset | None = None
        try:
            while True:
                try:
                    following = next(iterator)
                except StopIteration:
                    following = None

                if current.keep_with_next and following is not None:
                    combined = self._natural_height(current.image) + self.profile.gap_pt
                    combined += min(
                        self._natural_height(following.image),
                        self.profile.usable_height * 0.30,
                    )
                    if (
                        combined <= self.profile.usable_height
                        and combined > self.remaining_height
                    ):
                        self._record_seam_event(
                            current,
                            kind="padding",
                            method="keep-with-next",
                            source_y=0,
                            unused_height_pt=max(0.0, self.remaining_height),
                        )
                        self._advance_column()

                try:
                    self._place_asset(current)
                finally:
                    current.image.close()
                if following is None:
                    break
                current = following

            self._finish_document()
            return self.placements
        except Exception:
            current.image.close()
            if following is not None and following is not current:
                following.image.close()
            close_iterator = getattr(iterator, "close", None)
            if close_iterator is not None:
                close_iterator()
            raise

    @property
    def remaining_height(self) -> float:
        return self.cursor_y - self.profile.content_bottom

    def _natural_scale(self, image: Image.Image) -> float:
        return self.profile.column_width / image.width

    def _natural_height(self, image: Image.Image) -> float:
        return image.height * self._natural_scale(image)

    def _place_asset(self, asset: FlowAsset) -> None:
        image = asset.image
        natural_scale = self._natural_scale(image)
        minimum_chunk = max(48, int(image.height * 0.025))
        explicit_breaks = sorted(
            {
                int(round(value * image.height))
                for value in asset.safe_breaks
                if 0.0 < value < 1.0
            }
        )
        protected_ranges = [
            (
                int(round(start * image.height)),
                int(round(end * image.height)),
            )
            for start, end in asset.protected_ranges
        ]
        blank_rows = (
            _blank_row_flags(image) if asset.break_policy == "flow" else None
        )

        top = 0
        part = 1
        while top < image.height:
            remaining_pixels = image.height - top
            if remaining_pixels * natural_scale <= self.remaining_height + 0.01:
                self._draw_source_slice(
                    asset,
                    top,
                    image.height,
                    part=part,
                )
                return

            available_before = max(0.0, self.remaining_height)
            max_pixels = int(math.floor(available_before / natural_scale))
            cut, method = _choose_safe_cut(
                image_height=image.height,
                top=top,
                maximum=top + max_pixels,
                minimum_chunk=minimum_chunk,
                explicit_breaks=explicit_breaks,
                blank_rows=blank_rows,
                protected_ranges=protected_ranges,
            )
            if cut is not None:
                piece_height_pt = (cut - top) * natural_scale
                self._draw_source_slice(asset, top, cut, part=part)
                self._record_seam_event(
                    asset,
                    kind="split",
                    method=method or "safe",
                    source_y=cut,
                    unused_height_pt=max(0.0, available_before - piece_height_pt),
                )
                top = cut
                part += 1
                self._advance_column()
                continue

            fresh_column = (
                abs(self.remaining_height - self.profile.usable_height) <= 0.05
            )
            if not fresh_column:
                self._record_seam_event(
                    asset,
                    kind="padding",
                    method=(
                        "keep-together"
                        if asset.break_policy == "keep-together"
                        else "no-safe-band"
                    ),
                    source_y=top,
                    unused_height_pt=max(0.0, self.remaining_height),
                )
                self._advance_column()
                continue

            remainder = image.height - top
            fit_scale = self.profile.usable_height / remainder
            scale_fraction = fit_scale / natural_scale
            min_fraction = (
                asset.min_scale_fraction
                if asset.min_scale_fraction is not None
                else self.profile.min_scale_fraction
            )
            if scale_fraction + 1e-6 < min_fraction:
                raise CompositionError(
                    f"Asset {asset.asset_id} crosses a fresh column seam without a "
                    f"safe break; required scale fraction {scale_fraction:.3f} is "
                    f"below minimum {min_fraction:.3f}"
                )
            self._draw_source_slice(
                asset,
                top,
                image.height,
                part=part,
                forced_scale=fit_scale,
            )
            return

    def _draw_source_slice(
        self,
        asset: FlowAsset,
        top: int,
        bottom: int,
        *,
        part: int,
        forced_scale: float | None = None,
    ) -> None:
        whole_image = top == 0 and bottom == asset.image.height
        piece = (
            asset.image
            if whole_image
            else asset.image.crop((0, top, asset.image.width, bottom))
        )
        try:
            self._draw_image(
                asset,
                piece,
                part=part,
                forced_scale=forced_scale,
                source_pixel_y=(top, bottom),
            )
        finally:
            if not whole_image:
                piece.close()

    def _draw_image(
        self,
        asset: FlowAsset,
        image: Image.Image,
        *,
        part: int,
        forced_scale: float | None = None,
        source_pixel_y: tuple[int, int] | None = None,
    ) -> None:
        natural_scale = self._natural_scale(image)
        scale = forced_scale if forced_scale is not None else natural_scale
        width = image.width * scale
        height = image.height * scale
        if height > self.remaining_height + 0.05:
            raise CompositionError(f"Internal layout overflow for {asset.asset_id}")
        scale_fraction = scale / natural_scale
        x = self.profile.column_x(self.column) + (self.profile.column_width - width) / 2
        y = self.cursor_y - height
        effective_dpi = image.width / (width / 72.0)
        if effective_dpi + 0.01 < self.profile.min_effective_dpi:
            raise CompositionError(
                f"Asset {asset.asset_id} effective DPI {effective_dpi:.1f} is below "
                f"minimum {self.profile.min_effective_dpi:.1f}"
            )
        self.pdf.drawImage(
            ImageReader(image),
            x,
            y,
            width=width,
            height=height,
            preserveAspectRatio=True,
            mask="auto",
        )
        self.page_has_content = True
        self.placements.append(
            Placement(
                asset_id=asset.asset_id,
                role=asset.role,
                source_path=asset.source_path,
                page=self.page,
                column=self.column,
                bbox_points=(x, y, x + width, y + height),
                pixel_size=image.size,
                effective_dpi=effective_dpi,
                scale_fraction=scale_fraction,
                part=part,
                question_id=asset.question_id,
                bundle_id=asset.bundle_id,
                new_number=asset.new_number,
                original_number=asset.original_number,
                source_pixel_y=source_pixel_y,
                source_pixel_height=asset.image.height,
            )
        )
        self.cursor_y = y - self.profile.gap_pt

    def _record_seam_event(
        self,
        asset: FlowAsset,
        *,
        kind: str,
        method: str,
        source_y: int,
        unused_height_pt: float,
    ) -> None:
        next_page = self.page if self.column + 1 < self.profile.columns else self.page + 1
        next_column = self.column + 1 if self.column + 1 < self.profile.columns else 0
        self.seam_events.append(
            {
                "asset_id": asset.asset_id,
                "kind": kind,
                "method": method,
                "source_y": source_y,
                "from_page": self.page,
                "from_column": self.column + 1,
                "to_page": next_page,
                "to_column": next_column + 1,
                "unused_height_points": round(unused_height_pt, 3),
                "inside_protected_range": bool(
                    kind == "split"
                    and any(
                        start * asset.image.height <= source_y <= end * asset.image.height
                        for start, end in asset.protected_ranges
                    )
                ),
            }
        )

    def _advance_column(self) -> None:
        if self.column + 1 < self.profile.columns:
            self.column += 1
            self.cursor_y = self.profile.content_top
            return
        self._finish_page()
        self.page += 1
        self.column = 0
        self.cursor_y = self.profile.content_top
        self.page_has_content = False
        self._draw_header()

    def _draw_header(self) -> None:
        top = self.profile.height_pt - self.profile.margin_top_pt
        self.pdf.setFont(self.font_name, 13)
        self.pdf.drawString(self.profile.margin_left_pt, top - 14, self.title)
        if self.subtitle:
            self.pdf.setFont(self.font_name, 8.5)
            self.pdf.drawRightString(
                self.profile.width_pt - self.profile.margin_right_pt,
                top - 12,
                self.subtitle,
            )
        rule_y = self.profile.content_top + 4
        self.pdf.setLineWidth(0.5)
        self.pdf.line(
            self.profile.margin_left_pt,
            rule_y,
            self.profile.width_pt - self.profile.margin_right_pt,
            rule_y,
        )

    def _draw_footer(self) -> None:
        self.pdf.setFont(self.font_name, 8)
        self.pdf.drawCentredString(
            self.profile.width_pt / 2,
            self.profile.margin_bottom_pt,
            str(self.page),
        )

    def _finish_page(self) -> None:
        if not self.page_has_content:
            raise CompositionError("Layout attempted to emit a blank page")
        self._draw_footer()
        self.pdf.showPage()

    def _finish_document(self) -> None:
        if not self.page_has_content:
            raise CompositionError("Layout produced no content")
        self._draw_footer()
        self.pdf.save()


def _problem_flow_assets(
    manifest: dict[str, Any],
    selected: list[tuple[dict[str, Any], dict[str, Any]]],
    base_dir: Path,
    font_path: Path,
    renumber_actions: list[dict[str, Any]],
) -> Iterable[FlowAsset]:
    stimulus_index = {item["id"]: item for item in manifest.get("stimuli", [])}
    index = 0
    while index < len(selected):
        first_selection, first_question = selected[index]
        bundle_id = str(first_question.get("bundle_id") or first_question["id"])
        run_end = index + 1
        while run_end < len(selected):
            _next_selection, next_question = selected[run_end]
            next_bundle = str(next_question.get("bundle_id") or next_question["id"])
            if next_bundle != bundle_id:
                break
            run_end += 1
        run = selected[index:run_end]
        bundle_numbers = [int(item["new_number"]) for item, _question in run]
        if bundle_numbers != list(
            range(bundle_numbers[0], bundle_numbers[0] + len(bundle_numbers))
        ):
            raise CompositionError(
                f"Bundle run {bundle_id} does not have contiguous new numbers"
            )

        stimulus_ids: list[str] = []
        for _selection, question in run:
            for stimulus_id in question.get("stimulus_ids", []):
                if stimulus_id not in stimulus_ids:
                    stimulus_ids.append(stimulus_id)
        for stimulus_id in stimulus_ids:
            stimulus = stimulus_index[stimulus_id]
            yield from _load_asset_sequence(
                stimulus["assets"],
                base_dir,
                role="stimulus",
                owner_id=stimulus_id,
                bundle_id=bundle_id,
                range_renumber=stimulus.get("range_renumber"),
                range_numbers=bundle_numbers,
                font_path=font_path,
                renumber_actions=renumber_actions,
            )
        for selection, question in run:
            yield from _load_asset_sequence(
                question["assets"],
                base_dir,
                role="question",
                owner_id=question["id"],
                bundle_id=bundle_id,
                question_id=question["id"],
                original_number=int(question["original_number"]),
                new_number=int(selection["new_number"]),
                renumber=question.get("renumber"),
                font_path=font_path,
                renumber_actions=renumber_actions,
            )
        index = run_end


def _answer_flow_assets(
    manifest: dict[str, Any],
    selected: list[tuple[dict[str, Any], dict[str, Any]]],
    base_dir: Path,
    font_path: Path,
    renumber_actions: list[dict[str, Any]],
) -> Iterable[FlowAsset]:
    mode = manifest.get("answer", {}).get("mode", "key-and-solutions")
    if mode in {"key-only", "key-and-solutions"}:
        for chunk_number, key_image in enumerate(
            _iter_answer_key_images(selected, font_path), start=1
        ):
            yield FlowAsset(
                asset_id=f"answer-key-{chunk_number:02d}",
                role="answer_key",
                source_path=f"generated:answer-key:{chunk_number}",
                image=key_image,
                break_policy="keep-together",
                safe_breaks=(),
            )
    if mode in {"solutions-only", "key-and-solutions"}:
        for selection, question in selected:
            yield from _load_asset_sequence(
                question["solution_assets"],
                base_dir,
                role="solution",
                owner_id=f"{question['id']}_solution",
                bundle_id=str(question.get("bundle_id") or question["id"]),
                question_id=question["id"],
                original_number=int(question["original_number"]),
                new_number=int(selection["new_number"]),
                renumber=question.get("solution_renumber"),
                font_path=font_path,
                renumber_actions=renumber_actions,
            )


def _load_asset_sequence(
    values: list[Any],
    base_dir: Path,
    *,
    role: str,
    owner_id: str,
    bundle_id: str,
    font_path: Path,
    renumber_actions: list[dict[str, Any]],
    question_id: str | None = None,
    original_number: int | None = None,
    new_number: int | None = None,
    renumber: dict[str, Any] | None = None,
    range_renumber: dict[str, Any] | None = None,
    range_numbers: list[int] | None = None,
) -> list[FlowAsset]:
    result: list[FlowAsset] = []
    renumber_spec = renumber or range_renumber
    target_index = int(renumber_spec.get("asset_index", 0)) if renumber_spec else -1
    for index, value in enumerate(values):
        metadata = value if isinstance(value, dict) else {"path": value}
        source = _asset_path(value, base_dir)
        image = _open_normalized_image(source)
        source_height = image.height
        generated_prefix_height = 0
        if renumber_spec and index == target_index:
            if range_renumber is not None:
                numbers = sorted(set(range_numbers or []))
                if not numbers:
                    raise CompositionError(f"Stimulus {owner_id} has no new range numbers")
                template_key = "single_text_template" if len(numbers) == 1 else "text_template"
                default_template = "[{first}]" if len(numbers) == 1 else "[{first}~{last}]"
                template = str(range_renumber.get(template_key, default_template))
                replacement = template.format(first=numbers[0], last=numbers[-1])
            else:
                replacement = str(
                    renumber_spec.get("text_template", "{number}.")
                ).format(number=new_number)
            updated = _apply_renumber(
                image,
                renumber_spec,
                replacement_text=replacement,
                font_path=font_path,
                base_dir=base_dir,
                external_role=role,
            )
            image.close()
            image = updated
            if renumber_spec.get("mode") in {
                "source-number-absent",
                "mask-and-prepend",
            }:
                generated_prefix_height = max(0, image.height - source_height)
            renumber_actions.append(
                {
                    "owner_id": owner_id,
                    "role": role,
                    "asset_index": index,
                    "mode": renumber_spec["mode"],
                    "original_number": original_number,
                    "new_number": new_number,
                    "replacement_text": replacement,
                    "source_path": str(source),
                }
            )
        safe_breaks = [float(item) for item in metadata.get("safe_breaks", [])]
        protected_ranges = [
            (float(item[0]), float(item[1]))
            for item in metadata.get("protected_ranges", [])
        ]
        if generated_prefix_height:
            final_height = image.height
            safe_breaks = [
                (generated_prefix_height + value * source_height) / final_height
                for value in safe_breaks
            ]
            protected_ranges = [
                (
                    (generated_prefix_height + start * source_height) / final_height,
                    (generated_prefix_height + end * source_height) / final_height,
                )
                for start, end in protected_ranges
            ]
            protected_head = min(
                final_height,
                generated_prefix_height + max(80, int(source_height * 0.08)),
            )
            protected_ranges.append((0.0, protected_head / final_height))
        protected_ranges = _merge_normalized_ranges(protected_ranges)

        result.append(
            FlowAsset(
                asset_id=f"{owner_id}__{index + 1:02d}",
                role=role,
                source_path=str(source),
                image=image,
                break_policy=_resolve_break_policy(metadata),
                safe_breaks=tuple(safe_breaks),
                protected_ranges=tuple(protected_ranges),
                keep_with_next=bool(metadata.get("keep_with_next", False)),
                min_scale_fraction=(
                    float(metadata["min_scale_fraction"])
                    if "min_scale_fraction" in metadata
                    else None
                ),
                question_id=question_id,
                bundle_id=bundle_id,
                new_number=new_number,
                original_number=original_number,
            )
        )
    return result


def _apply_renumber(
    image: Image.Image,
    spec: dict[str, Any],
    *,
    replacement_text: str,
    font_path: Path,
    base_dir: Path,
    external_role: str,
) -> Image.Image:
    """Return an image with a source number safely replaced or prefixed."""

    mode = spec.get("mode", "preserve")
    if mode == "preserve":
        return image.copy()
    if mode in {"source-number-absent", "mask-and-prepend"}:
        prepared = image.copy().convert("RGB")
        if mode == "mask-and-prepend":
            bbox = _normalized_bbox(spec.get("bbox"), "renumber.bbox")
            left = int(round(bbox[0] * prepared.width))
            top = int(round(bbox[1] * prepared.height))
            right = int(round(bbox[2] * prepared.width))
            bottom = int(round(bbox[3] * prepared.height))
            background = ImageColor.getrgb(str(spec.get("background", "#FFFFFF")))
            ImageDraw.Draw(prepared).rectangle(
                (left, top, right, bottom), fill=background
            )
        label = replacement_text
        if external_role == "solution" and "label_suffix" not in spec:
            label += " 해설"
        elif spec.get("label_suffix"):
            label += str(spec["label_suffix"])
        result = _prepend_label(prepared, label, font_path)
        prepared.close()
        return result
    if mode == "mask-in-place":
        return _mask_and_draw_in_place(
            image,
            spec,
            replacement_text=replacement_text,
            default_font_path=font_path,
            base_dir=base_dir,
        )
    if mode != "mask":
        raise CompositionError(f"Unsupported renumber mode: {mode}")

    bbox = _normalized_bbox(spec.get("bbox"), "renumber.bbox")
    result = image.copy().convert("RGB")
    left = int(round(bbox[0] * result.width))
    top = int(round(bbox[1] * result.height))
    right = int(round(bbox[2] * result.width))
    bottom = int(round(bbox[3] * result.height))
    background = ImageColor.getrgb(str(spec.get("background", "#FFFFFF")))
    foreground = ImageColor.getrgb(str(spec.get("foreground", "#000000")))
    draw = ImageDraw.Draw(result)
    draw.rectangle((left, top, right, bottom), fill=background)
    padding = max(1, int((bottom - top) * float(spec.get("padding_fraction", 0.04))))
    _draw_text_fitted(
        draw,
        replacement_text,
        (left + padding, top + padding, right - padding, bottom - padding),
        font_path,
        foreground,
        align=str(spec.get("align", "left")),
    )
    return result


def _mask_and_draw_in_place(
    image: Image.Image,
    spec: dict[str, Any],
    *,
    replacement_text: str,
    default_font_path: Path,
    base_dir: Path,
) -> Image.Image:
    """Replace a printed number at its source anchor without adding a title row.

    The source glyph's right edge and ink bottom are treated as the stable
    anchor.  Longer global numbers therefore grow into the left margin instead
    of colliding with the first line of the question.  Optional provenance is
    drawn in the already-blank margin immediately above that anchor.
    """

    bbox = _normalized_bbox(spec.get("bbox"), "renumber.bbox")
    result = image.copy().convert("RGB")
    left = int(round(bbox[0] * result.width))
    top = int(round(bbox[1] * result.height))
    right = int(round(bbox[2] * result.width))
    bottom = int(round(bbox[3] * result.height))
    if right <= left or bottom <= top:
        raise CompositionError("renumber.bbox has no drawable area")

    # Measure the actual printed glyph before erasing its padded detector box.
    source_crop = result.crop((left, top, right, bottom)).convert("L")
    try:
        source_ink = source_crop.point(lambda value: 255 if value < 205 else 0)
        try:
            local_ink_bbox = source_ink.getbbox()
        finally:
            source_ink.close()
    finally:
        source_crop.close()
    if local_ink_bbox is None:
        raise CompositionError("renumber.bbox contains no source-number ink")

    source_ink_left = left + local_ink_bbox[0]
    source_ink_top = top + local_ink_bbox[1]
    source_ink_right = left + local_ink_bbox[2]
    source_ink_bottom = top + local_ink_bbox[3]
    source_ink_height = max(1, source_ink_bottom - source_ink_top)

    background = ImageColor.getrgb(str(spec.get("background", "#FFFFFF")))
    foreground = ImageColor.getrgb(str(spec.get("foreground", "#111111")))
    draw = ImageDraw.Draw(result)
    draw.rectangle((left, top, right, bottom), fill=background)

    number_font_path = _replacement_font_path(
        spec,
        "number_font_path",
        default_font_path,
        base_dir,
    )
    target_number_height = max(
        6,
        int(
            round(
                source_ink_height
                * float(spec.get("number_ink_height_scale", 1.0))
            )
        ),
    )
    number_font, number_box, _ = _font_for_ink_height(
        replacement_text,
        number_font_path,
        target_number_height,
        max_width=source_ink_right,
    )
    number_x = source_ink_right - number_box[2]
    number_y = source_ink_bottom - number_box[3]
    if number_x + number_box[0] < 0:
        raise CompositionError(
            f"Replacement number extends past the image edge: {replacement_text}"
        )
    draw.text(
        (number_x, number_y),
        replacement_text,
        font=number_font,
        fill=foreground,
    )

    annotation = str(spec.get("annotation_text", "")).strip()
    if annotation:
        annotation_font_path = _replacement_font_path(
            spec,
            "annotation_font_path",
            number_font_path,
            base_dir,
        )
        annotation_scale = float(spec.get("annotation_scale", 0.5))
        annotation_height = max(6, int(round(source_ink_height * annotation_scale)))
        annotation_font, annotation_box, _ = _font_for_ink_height(
            annotation,
            annotation_font_path,
            annotation_height,
            max_width=result.width,
        )
        annotation_width = annotation_box[2] - annotation_box[0]
        # Use the same right anchor when it fits; otherwise keep the complete
        # label inside the left edge while retaining the original top margin.
        annotation_x = max(0, source_ink_right - annotation_box[2])
        gap = max(
            2,
            int(
                round(
                    source_ink_height
                    * float(spec.get("annotation_gap_fraction", 0.22))
                )
            ),
        )
        annotation_bottom = source_ink_top - gap
        annotation_y = annotation_bottom - annotation_box[3]
        if annotation_y + annotation_box[1] < 0:
            raise CompositionError(
                "Insufficient top margin for the requested execution-date label"
            )
        if annotation_x + annotation_width > result.width:
            raise CompositionError("Execution-date label does not fit the image width")
        annotation_foreground = ImageColor.getrgb(
            str(spec.get("annotation_foreground", "#4A4A4A"))
        )
        draw.text(
            (annotation_x, annotation_y),
            annotation,
            font=annotation_font,
            fill=annotation_foreground,
        )
    return result


def _replacement_font_path(
    spec: dict[str, Any],
    key: str,
    default: Path,
    base_dir: Path,
) -> Path:
    raw = spec.get(key)
    if raw is None:
        return default
    path = Path(str(raw))
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Renumber font not found: {path}")
    return path


@lru_cache(maxsize=512)
def _cached_truetype(font_path: str, font_size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, font_size)


def _font_for_ink_height(
    text: str,
    font_path: Path,
    target_height: int,
    *,
    max_width: int,
) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int], int]:
    """Choose a font size by rendered ink height, with a bounded width fallback."""

    upper_size = max(12, int(round(target_height * 2.4)))
    candidates: list[
        tuple[int, int, int, ImageFont.FreeTypeFont, tuple[int, int, int, int]]
    ] = []
    for font_size in range(6, upper_size + 1):
        font = _cached_truetype(str(font_path), font_size)
        text_box = font.getbbox(text)
        ink_width = text_box[2] - text_box[0]
        ink_height = text_box[3] - text_box[1]
        if ink_width <= max_width:
            candidates.append(
                (
                    abs(ink_height - target_height),
                    -ink_height,
                    -font_size,
                    font,
                    text_box,
                )
            )
    if not candidates:
        raise CompositionError(f"Text does not fit the available width: {text}")
    _, _, neg_size, font, text_box = min(candidates, key=lambda item: item[:3])
    return font, text_box, -neg_size


def _prepend_label(image: Image.Image, label: str, font_path: Path) -> Image.Image:
    label_height = max(44, int(round(image.width * 0.075)))
    result = Image.new("RGB", (image.width, image.height + label_height), "white")
    result.paste(image, (0, label_height))
    draw = ImageDraw.Draw(result)
    _draw_text_fitted(
        draw,
        label,
        (0, 2, image.width, label_height - 3),
        font_path,
        (0, 0, 0),
        align="left",
    )
    return result


def _draw_text_fitted(
    draw: ImageDraw.ImageDraw,
    text: str,
    bbox: tuple[int, int, int, int],
    font_path: Path,
    fill: tuple[int, int, int],
    *,
    align: str,
) -> None:
    left, top, right, bottom = bbox
    width = max(1, right - left)
    height = max(1, bottom - top)
    font_size = max(6, int(height * 0.92))
    while font_size >= 6:
        font = ImageFont.truetype(str(font_path), font_size)
        text_box = draw.textbbox((0, 0), text, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        if text_width <= width and text_height <= height:
            break
        font_size -= 1
    else:
        raise CompositionError(f"Replacement number does not fit bbox: {text}")
    if align == "center":
        x = left + (width - text_width) / 2
    elif align == "right":
        x = right - text_width
    else:
        x = left
    y = top + (height - text_height) / 2 - text_box[1]
    draw.text((x, y), text, font=font, fill=fill)


def _answer_key_images(
    selected: list[tuple[dict[str, Any], dict[str, Any]]],
    font_path: Path,
) -> list[Image.Image]:
    """Materialize readable answer-key chunks (primarily for callers/tests)."""

    return list(_iter_answer_key_images(selected, font_path))


def _iter_answer_key_images(
    selected: list[tuple[dict[str, Any], dict[str, Any]]],
    font_path: Path,
) -> Iterable[Image.Image]:
    """Yield bounded answer-key chunks without retaining all bitmaps at once.

    A single tall bitmap becomes unreadable or violates the layout scale floor
    when a question bank contains hundreds of entries.  Each generated image
    is therefore bounded to a size that fits in one B4 column at the normal
    composition scale.  The iterator also preserves the writer's two-bitmap
    memory bound when very large banks require many answer-key chunks.
    """

    if not selected:
        return
    for start in range(0, len(selected), ANSWER_KEY_CHUNK_SIZE):
        yield _answer_key_image(
            selected[start:start + ANSWER_KEY_CHUNK_SIZE],
            font_path,
            first_number=int(selected[start][0]["new_number"]),
            total_count=len(selected),
        )


def _answer_key_image(
    selected: list[tuple[dict[str, Any], dict[str, Any]]],
    font_path: Path,
    *,
    first_number: int = 1,
    total_count: int | None = None,
) -> Image.Image:
    width = 1600
    columns = min(5, max(1, len(selected)))
    rows = math.ceil(len(selected) / columns)
    cell_width = width // columns
    cell_height = 82
    title_height = 92
    height = title_height + rows * cell_height + 30
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font_path), 46)
    cell_font = ImageFont.truetype(str(font_path), 36)
    total = total_count if total_count is not None else len(selected)
    last_number = first_number + len(selected) - 1
    title = "정답표"
    if total > len(selected):
        title += f" ({first_number}-{last_number}번)"
    draw.text((24, 18), title, font=title_font, fill="black")
    for index, (selection, question) in enumerate(selected):
        row = index // columns
        column = index % columns
        x0 = column * cell_width
        y0 = title_height + row * cell_height
        x1 = x0 + cell_width
        y1 = y0 + cell_height
        draw.rectangle((x0, y0, x1, y1), outline=(70, 70, 70), width=2)
        text = f"{selection['new_number']}.  {question['answer']}"
        draw.text((x0 + 18, y0 + 17), text, font=cell_font, fill="black")
    return image


def _choose_safe_cut(
    *,
    image_height: int,
    top: int,
    maximum: int,
    minimum_chunk: int,
    explicit_breaks: list[int],
    blank_rows: list[bool] | None,
    protected_ranges: list[tuple[int, int]],
) -> tuple[int | None, str | None]:
    lower = top + minimum_chunk
    upper = min(maximum, image_height - minimum_chunk)
    if upper < lower:
        return None, None

    explicit = [
        position
        for position in explicit_breaks
        if lower <= position <= upper
        and not _inside_protected_range(position, protected_ranges)
    ]
    if explicit:
        return max(explicit), "explicit-safe-break"
    if blank_rows is None:
        return None, None

    minimum_band = max(6, min(30, minimum_chunk // 5))
    bands: list[tuple[int, int]] = []
    band_start: int | None = None
    for y in range(max(0, lower), min(image_height, upper + 1)):
        available = blank_rows[y] and not _inside_protected_range(
            y, protected_ranges
        )
        if available and band_start is None:
            band_start = y
        elif not available and band_start is not None:
            if y - band_start >= minimum_band:
                bands.append((band_start, y))
            band_start = None
    if band_start is not None and upper + 1 - band_start >= minimum_band:
        bands.append((band_start, upper + 1))
    if not bands:
        return None, None
    latest = max(bands, key=lambda item: (item[1], item[1] - item[0]))
    cut = max(latest[0] + 1, latest[1] - max(2, minimum_band // 2))
    return cut, "detected-whitespace"


def _blank_row_flags(image: Image.Image) -> list[bool]:
    scan_width = min(160, image.width)
    gray = image.convert("L").resize((scan_width, image.height))
    try:
        flattened = getattr(gray, "get_flattened_data", None)
        pixels = list(flattened() if flattened is not None else gray.getdata())
    finally:
        gray.close()
    blank_rows: list[bool] = []
    for y in range(image.height):
        offset = y * scan_width
        dark = sum(1 for value in pixels[offset : offset + scan_width] if value < 242)
        blank_rows.append(dark / scan_width <= 0.006)
    return blank_rows


def _inside_protected_range(
    position: int, protected_ranges: list[tuple[int, int]]
) -> bool:
    return any(start <= position <= end for start, end in protected_ranges)


def _verify_and_render_pdf(
    pdf_path: Path,
    profile: PaperProfile,
    qa_dir: Path | None,
) -> dict[str, Any]:
    document = pdfium.PdfDocument(str(pdf_path))
    page_sizes_match = True
    blank_pages = 0 if qa_dir is not None else None
    try:
        page_count = len(document)
        if page_count <= 0:
            raise CompositionError(f"PDF has no pages: {pdf_path}")
        if qa_dir is not None:
            qa_dir.mkdir(parents=True, exist_ok=True)
            for old in qa_dir.glob("page_*.png"):
                old.unlink()
        for page_index in range(page_count):
            page = document[page_index]
            bitmap = None
            try:
                width, height = page.get_size()
                if (
                    abs(float(width) - profile.width_pt) > 0.75
                    or abs(float(height) - profile.height_pt) > 0.75
                ):
                    page_sizes_match = False
                    raise CompositionError(
                        f"Unexpected page size in {pdf_path.name} page {page_index + 1}: "
                        f"{width:.2f}x{height:.2f} pt"
                    )
                if qa_dir is not None:
                    bitmap = page.render(scale=profile.qa_dpi / 72.0)
                    image = bitmap.to_pil().convert("RGB")
                    try:
                        gray = image.convert("L")
                        try:
                            extrema = gray.getextrema()
                        finally:
                            gray.close()
                        if extrema[0] > 248:
                            assert blank_pages is not None
                            blank_pages += 1
                            raise CompositionError(
                                f"Blank rendered page: {pdf_path.name} page {page_index + 1}"
                            )
                        image.save(
                            qa_dir / f"page_{page_index + 1:04d}.png", format="PNG"
                        )
                    finally:
                        image.close()
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
    finally:
        document.close()
    return {
        "opened": True,
        "page_count": page_count,
        "page_sizes_match": page_sizes_match,
        "blank_pages": blank_pages,
        "blank_pages_checked": qa_dir is not None,
    }


def _ordered_unique_numbers(
    placements: Iterable[Placement], role: str
) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for placement in placements:
        if placement.role != role or placement.new_number is None:
            continue
        number = int(placement.new_number)
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result


def _count_out_of_bounds(
    placements: Iterable[Placement], profile: PaperProfile
) -> int:
    tolerance = 0.75
    failures = 0
    for item in placements:
        left, bottom, right, top = item.bbox_points
        column_left = profile.column_x(item.column)
        column_right = column_left + profile.column_width
        if (
            left < column_left - tolerance
            or right > column_right + tolerance
            or bottom < profile.content_bottom - tolerance
            or top > profile.content_top + tolerance
            or left < -tolerance
            or bottom < -tolerance
            or right > profile.width_pt + tolerance
            or top > profile.height_pt + tolerance
            or right <= left
            or top <= bottom
        ):
            failures += 1
    return failures


def _source_ranges_contiguous(placements: Iterable[Placement]) -> bool:
    groups: dict[str, list[Placement]] = {}
    for item in placements:
        groups.setdefault(item.asset_id, []).append(item)
    if not groups:
        return False
    for items in groups.values():
        if any(
            item.source_pixel_y is None or item.source_pixel_height is None
            for item in items
        ):
            return False
        cursor = 0
        expected_part = 1
        source_height = int(items[0].source_pixel_height or 0)
        if source_height <= 0:
            return False
        for item in items:
            if int(item.source_pixel_height or 0) != source_height:
                return False
            start, end = item.source_pixel_y or (0, 0)
            if item.part == 1 and cursor == source_height:
                cursor = 0
                expected_part = 1
            if item.part != expected_part:
                return False
            if start != cursor or end <= start or end > source_height:
                return False
            cursor = end
            expected_part += 1
        if cursor != source_height:
            return False
    return True


def _count_protected_splits(seam_events: Iterable[dict[str, Any]]) -> int:
    return sum(
        1
        for event in seam_events
        if event.get("kind") == "split"
        and bool(event.get("inside_protected_range"))
    )


def _validate_source_label_choice(value: Any) -> str:
    if not isinstance(value, dict) or "mode" not in value:
        raise CompositionError(
            "source_label.mode is required. Before composition, ask the user to "
            "choose none, year, or year-month."
        )
    mode = str(value["mode"]).strip().lower()
    if mode not in SOURCE_LABEL_MODES:
        raise CompositionError(
            f"source_label.mode must be one of {sorted(SOURCE_LABEL_MODES)}"
        )
    return mode


def _validate_question_source_label(
    renumber: Any,
    *,
    mode: str,
    label: str,
) -> None:
    annotation = (
        str(renumber.get("annotation_text", "")).strip()
        if isinstance(renumber, dict)
        else ""
    )
    if mode == "none":
        if annotation:
            raise CompositionError(
                f"{label} has annotation_text although source_label.mode is none"
            )
        return
    if not isinstance(renumber, dict) or renumber.get("mode") != "mask-in-place":
        raise CompositionError(
            f"{label} must use mask-in-place when source_label.mode is {mode}"
        )
    pattern = SOURCE_YEAR_PATTERN if mode == "year" else SOURCE_YEAR_MONTH_PATTERN
    expected = "YYYY년" if mode == "year" else "YYYY년 MM월 시행"
    if not pattern.fullmatch(annotation):
        raise CompositionError(
            f"{label}.renumber.annotation_text must match {expected} for "
            f"source_label.mode {mode}"
        )


def _validate_renumber(
    spec: Any,
    *,
    original_number: int,
    new_number: int,
    asset_count: int,
    label: str,
) -> None:
    if spec is None:
        if original_number != new_number:
            raise CompositionError(
                f"{label} changes {original_number} -> {new_number} but has no "
                "renumber rule"
            )
        return
    _validate_replacement_spec(
        spec,
        asset_count=asset_count,
        label=f"{label} renumber",
        allow_preserve=True,
    )
    mode = spec.get("mode")
    if original_number != new_number and mode == "preserve":
        raise CompositionError(
            f"{label} cannot preserve original number {original_number} when new "
            f"number is {new_number}"
        )


def _validate_replacement_spec(
    spec: Any,
    *,
    asset_count: int,
    label: str,
    allow_preserve: bool,
) -> None:
    if not isinstance(spec, dict):
        raise CompositionError(f"{label} must be an object")
    allowed = {
        "mask",
        "mask-in-place",
        "source-number-absent",
        "mask-and-prepend",
    }
    if allow_preserve:
        allowed.add("preserve")
    mode = spec.get("mode")
    if mode not in allowed:
        raise CompositionError(f"{label} has unsupported mode: {mode}")
    asset_index = int(spec.get("asset_index", 0))
    if asset_index < 0 or asset_index >= asset_count:
        raise CompositionError(f"{label}.asset_index is out of range")
    if mode in {"mask", "mask-in-place", "mask-and-prepend"}:
        _normalized_bbox(spec.get("bbox"), f"{label}.bbox")
    if mode == "mask-in-place":
        annotation_scale = float(spec.get("annotation_scale", 0.5))
        if not 0.1 <= annotation_scale <= 1.0:
            raise CompositionError(
                f"{label}.annotation_scale must be between 0.1 and 1.0"
            )
        number_scale = float(spec.get("number_ink_height_scale", 1.0))
        if not 0.5 <= number_scale <= 1.5:
            raise CompositionError(
                f"{label}.number_ink_height_scale must be between 0.5 and 1.5"
            )
        gap_fraction = float(spec.get("annotation_gap_fraction", 0.22))
        if not 0.0 <= gap_fraction <= 2.0:
            raise CompositionError(
                f"{label}.annotation_gap_fraction must be between 0 and 2"
            )
        for key in ("number_font_path", "annotation_font_path"):
            if key in spec and not str(spec[key]).strip():
                raise CompositionError(f"{label}.{key} cannot be empty")


def _validate_asset_spec(value: Any) -> None:
    if isinstance(value, str):
        if not value.strip():
            raise CompositionError("Asset path cannot be empty")
        return
    if not isinstance(value, dict) or not str(value.get("path", "")).strip():
        raise CompositionError("Every asset must be a path string or an object with path")
    safe_breaks = value.get("safe_breaks", [])
    if not isinstance(safe_breaks, list) or any(
        not 0.0 < float(item) < 1.0 for item in safe_breaks
    ):
        raise CompositionError("asset.safe_breaks must contain normalized values inside 0..1")
    break_policy = _resolve_break_policy(value)
    if break_policy == "keep-together" and safe_breaks:
        raise CompositionError(
            "keep-together assets cannot also declare safe_breaks"
        )
    protected_ranges = value.get("protected_ranges", [])
    if not isinstance(protected_ranges, list):
        raise CompositionError("asset.protected_ranges must be an array")
    normalized_ranges: list[tuple[float, float]] = []
    for item in protected_ranges:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise CompositionError(
                "asset.protected_ranges entries must contain [y0, y1]"
            )
        y0, y1 = (float(number) for number in item)
        if not 0.0 <= y0 < y1 <= 1.0:
            raise CompositionError(
                "asset.protected_ranges must satisfy 0 <= y0 < y1 <= 1"
            )
        normalized_ranges.append((y0, y1))
    ordered_ranges = sorted(normalized_ranges)
    if any(
        current[0] < previous[1]
        for previous, current in zip(ordered_ranges, ordered_ranges[1:])
    ):
        raise CompositionError("asset.protected_ranges cannot overlap")
    if "min_scale_fraction" in value:
        fraction = float(value["min_scale_fraction"])
        if not 0.25 <= fraction <= 1.0:
            raise CompositionError("asset.min_scale_fraction must be inside 0.25..1")


def _normalized_bbox(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise CompositionError(f"{label} must contain four normalized numbers")
    bbox = tuple(float(item) for item in value)
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise CompositionError(f"{label} must satisfy 0 <= x0 < x1 <= 1 and y likewise")
    return bbox


def _unique_index(items: list[Any], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise CompositionError(f"Every {label} item requires id")
        item_id = str(item["id"])
        if item_id in result:
            raise CompositionError(f"Duplicate {label} id: {item_id}")
        result[item_id] = item
    return result


def _asset_path(value: Any, base_dir: Path) -> Path:
    if isinstance(value, str):
        raw = value
    elif isinstance(value, dict) and str(value.get("path", "")).strip():
        raw = str(value["path"])
    else:
        raise CompositionError("Every asset must be a path string or an object with path")
    path = Path(raw)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _open_normalized_image(path: Path) -> Image.Image:
    """Load one asset in visual orientation on an opaque white RGB canvas."""

    with Image.open(path) as loaded:
        oriented = ImageOps.exif_transpose(loaded)
        try:
            if oriented.width <= 0 or oriented.height <= 0:
                raise CompositionError(f"Image asset has invalid dimensions: {path}")
            if oriented.width * oriented.height > 200_000_000:
                raise CompositionError(f"Image asset is too large to compose safely: {path}")
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                try:
                    background = Image.new("RGBA", rgba.size, "white")
                    try:
                        background.alpha_composite(rgba)
                        return background.convert("RGB")
                    finally:
                        background.close()
                finally:
                    rgba.close()
            return oriented.convert("RGB")
        finally:
            if oriented is not loaded:
                oriented.close()


def _font_path(manifest: dict[str, Any], base_dir: Path) -> Path:
    raw = manifest.get("font_path")
    if raw:
        path = Path(str(raw))
        if not path.is_absolute():
            path = base_dir / path
    else:
        path = Path(r"C:\Windows\Fonts\malgun.ttf")
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"Korean-capable TrueType font not found; set font_path: {path}"
        )
    return path


def _register_pdf_font(font_path: Path) -> str:
    name = "ExamPaperMalgun"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return name


def _materialize_manifest_paths(
    manifest: dict[str, Any], base_dir: Path
) -> dict[str, Any]:
    result = copy.deepcopy(manifest)
    for item in [*result.get("questions", []), *result.get("stimuli", [])]:
        for key in ("assets", "solution_assets"):
            values = item.get(key, []) or []
            for index, value in enumerate(values):
                absolute = str(_asset_path(value, base_dir))
                if isinstance(value, str):
                    values[index] = absolute
                else:
                    value["path"] = absolute
        for key in ("renumber", "solution_renumber", "range_renumber"):
            spec = item.get(key)
            if not isinstance(spec, dict):
                continue
            for font_key in ("number_font_path", "annotation_font_path"):
                raw = spec.get(font_key)
                if raw is None:
                    continue
                font = Path(str(raw))
                if not font.is_absolute():
                    font = base_dir / font
                spec[font_key] = str(font.resolve())
    return result


def _profile_dict(profile: PaperProfile) -> dict[str, Any]:
    return {
        "size": profile.name,
        "orientation": profile.orientation,
        "width_mm": round(profile.width_pt / MM_TO_POINTS, 3),
        "height_mm": round(profile.height_pt / MM_TO_POINTS, 3),
        "columns": profile.columns,
        "column_width_mm": round(profile.column_width / MM_TO_POINTS, 3),
        "usable_height_mm": round(profile.usable_height / MM_TO_POINTS, 3),
        "min_effective_dpi": profile.min_effective_dpi,
        "flow_mode": profile.flow_mode,
    }


def _resolve_break_policy(metadata: dict[str, Any]) -> str:
    if "break_policy" in metadata:
        policy = str(metadata["break_policy"])
    elif bool(metadata.get("auto_split", True)):
        policy = "flow"
    elif metadata.get("safe_breaks"):
        policy = "safe-only"
    else:
        policy = "keep-together"
    if policy not in BREAK_POLICIES:
        raise CompositionError(
            f"asset.break_policy must be one of {sorted(BREAK_POLICIES)}"
        )
    return policy


def _merge_normalized_ranges(
    ranges: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    ordered = sorted(ranges)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _safe_build_id(value: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", value.strip())
    if not sanitized or sanitized in {".", ".."}:
        raise CompositionError("build_id has no safe filename characters")
    return sanitized


def _require_direct_child(path: Path, root: Path) -> None:
    if path.resolve().parent != root.resolve():
        raise CompositionError(f"Internal build path escaped output root: {path}")


def _remove_internal_path(path: Path, root: Path) -> None:
    _require_direct_child(path, root)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _close_flow_assets(assets: Iterable[FlowAsset]) -> None:
    for asset in assets:
        asset.image.close()
