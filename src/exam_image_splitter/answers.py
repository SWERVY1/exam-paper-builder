"""Answer-sheet-specific structural proposals.

The question-paper splitter assumes a two-column exam sheet.  Korean answer
and explanation PDFs instead occur as compact three-column sheets, detailed
single-column explanations, and (for one historical document) two-page
landscape spreads.  This module detects only numbered heading geometry and
constructs ordinary manifests so the established renderer, trimming, and
assembly pipeline can be reused.
"""

from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pypdfium2 as pdfium

from .manifest import validate_manifest


COMPACT_HEADING_PATTERN = re.compile(
    r"(?<!\d)(\d{1,3})\s*[.]?\s*[\[{]?\s*출제\s*의도"
)
NUMBERED_HEADING_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*[.]")
CIRCLED_CHOICES = tuple("①②③④⑤⑥⑦⑧⑨⑩")
ANSWER_ENTRY_PATTERN = re.compile(
    r"(?<!\d)(\d{1,3})\s*(?:[.)]|번)?\s*([①②③④⑤])"
)
SECTION_MARKER_PATTERN = re.compile(
    r"(?P<subject>물리|화학|생물|생명과학|지구과학)\s*"
    r"(?P<level>Ⅰ|Ⅱ|I{1,2})\s*정\s*답"
)


@dataclass(frozen=True)
class AnswerLayout:
    name: str
    columns: tuple[tuple[float, float], ...]
    content_top: float
    content_bottom: float
    start_content_top: float | None = None
    # Keep just enough lead-in for the heading glyph while avoiding the tail
    # of the previous compact-column explanation.
    start_margin: float = 0.005
    # Detailed answer lines can sit immediately above the next heading.  Keep
    # a small blank band while preserving their lower strokes and formulae.
    end_margin: float = 0.005

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def column_for_x(self, x: float) -> int | None:
        for index, (x0, x1) in enumerate(self.columns):
            if x0 <= x <= x1:
                return index
        return None


COMPACT_THREE_COLUMN = AnswerLayout(
    name="compact_three_column",
    columns=((0.055, 0.343), (0.350, 0.610), (0.620, 0.940)),
    content_top=0.135,
    content_bottom=0.910,
    start_content_top=0.120,
)
COMPACT_TWO_COLUMN = AnswerLayout(
    name="compact_two_column",
    columns=((0.090, 0.450), (0.490, 0.930)),
    content_top=0.095,
    content_bottom=0.910,
    start_content_top=0.085,
)
COMPACT_WIDE_THREE_COLUMN = AnswerLayout(
    name="compact_wide_three_column",
    columns=((0.060, 0.360), (0.370, 0.630), (0.640, 0.940)),
    content_top=0.135,
    content_bottom=0.910,
    start_content_top=0.120,
)
COMPACT_DENSE_THREE_COLUMN = AnswerLayout(
    name="compact_dense_three_column",
    columns=((0.060, 0.300), (0.315, 0.555), (0.565, 0.940)),
    content_top=0.120,
    content_bottom=0.910,
    start_content_top=0.105,
)
DETAILED_SINGLE_COLUMN = AnswerLayout(
    name="detailed_single_column",
    columns=((0.100, 0.910),),
    content_top=0.120,
    content_bottom=0.900,
    start_content_top=0.095,
)
LANDSCAPE_SPREAD = AnswerLayout(
    name="landscape_two_page_spread",
    columns=((0.050, 0.470), (0.530, 0.950)),
    content_top=0.120,
    content_bottom=0.910,
    start_content_top=0.095,
)


@dataclass(frozen=True)
class AnswerAnchor:
    number: int
    page: int
    column: int
    bbox: tuple[float, float, float, float]

    @property
    def position(self) -> tuple[int, int, float]:
        return self.page, self.column, self.bbox[1]


@dataclass(frozen=True)
class SectionMarker:
    subject: str
    page: int
    bbox: tuple[float, float, float, float]

    @property
    def position(self) -> tuple[int, float]:
        return self.page, self.bbox[1]


def propose_solution_manifest(
    source_pdf: str | Path,
    output_manifest: str | Path,
    exam_id: str,
    *,
    dpi: int = 300,
    output_width_px: int = 1080,
    output_format: str = "webp",
    export_filenames: dict[int, str] | None = None,
    expected_count: int = 20,
    store_fragments: bool = False,
    subject_section: str | None = None,
    sequence_ordinal: int | None = None,
) -> dict[str, Any]:
    """Create a manifest with one explanatory image for every expected item."""

    if expected_count <= 0:
        raise ValueError("expected_count must be positive")

    source = Path(source_pdf).resolve()
    output_path = Path(output_manifest).resolve()
    layout, anchors, page_count = detect_solution_anchors(
        source,
        expected_count=expected_count,
        subject_section=subject_section,
        sequence_ordinal=sequence_ordinal,
    )
    section_end = (
        _section_end_anchor(source, subject_section, layout)
        if subject_section
        else None
    )
    document_end_region = _document_content_end_region(source, layout, page_count)
    expected_numbers = list(range(1, expected_count + 1))
    if [anchor.number for anchor in anchors] != expected_numbers:
        raise ValueError(
            f"Answer heading sequence must be exactly 1..{expected_count}"
        )

    fragments: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else section_end
        fragment_ids = _append_answer_span(
            fragments,
            prefix=f"ans_q{anchor.number:03d}",
            start=anchor,
            end=end,
            layout=layout,
            page_count=page_count,
            document_end_region=document_end_region,
        )
        if not fragment_ids:
            raise ValueError(f"Question {anchor.number} produced no answer fragments")
        question: dict[str, Any] = {
            "id": f"Q{anchor.number:03d}",
            "section": "solution",
            "number": anchor.number,
            "stimulus_ids": [],
            "fragment_ids": fragment_ids,
        }
        if export_filenames and anchor.number in export_filenames:
            question["export"] = {"filename": export_filenames[anchor.number]}
        questions.append(question)

    relative_source = os.path.relpath(source, output_path.parent).replace("\\", "/")
    manifest: dict[str, Any] = {
        "version": 1,
        "exam_id": exam_id,
        "source_pdf": relative_source,
        "review_status": "needs_review",
        "proposal": {
            "profile": layout.name,
            "question_anchor_count": len(anchors),
            "expected_question_count": expected_count,
            "body_text_stored": False,
            "subject_section": subject_section,
            "sequence_ordinal": sequence_ordinal,
        },
        "render": {
            "dpi": dpi,
            "output_width_px": output_width_px,
            "padding_px": 60,
            "gap_px": 36,
            "background": "#FFFFFF",
            "max_output_height_px": 20000,
            "min_ink_ratio": 0.002,
            "output_format": output_format,
            "store_fragments": store_fragments,
        },
        "fragments": fragments,
        "stimuli": [],
        "questions": questions,
    }
    validate_manifest(manifest, page_count=page_count)
    return manifest


def detect_solution_anchors(
    source_pdf: str | Path,
    *,
    expected_count: int = 20,
    subject_section: str | None = None,
    sequence_ordinal: int | None = None,
) -> tuple[AnswerLayout, list[AnswerAnchor], int]:
    """Detect answer headings without retaining the answer text itself."""

    if expected_count <= 0:
        raise ValueError("expected_count must be positive")
    if sequence_ordinal is not None and sequence_ordinal <= 0:
        raise ValueError("sequence_ordinal must be positive")
    expected_numbers = range(1, expected_count + 1)

    source = Path(source_pdf).resolve()
    document = pdfium.PdfDocument(str(source))
    try:
        page_count = len(document)
        page_aspects: list[float] = []
        compact_raw: list[tuple[int, int, tuple[float, float, float, float]]] = []
        generic: list[tuple[int, int, float, float, str]] = []
        section_markers: list[SectionMarker] = []
        for page_index in range(page_count):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                page_bbox = tuple(float(value) for value in page.get_bbox())
                width = page_bbox[2] - page_bbox[0]
                height = page_bbox[3] - page_bbox[1]
                if width <= 0 or height <= 0:
                    raise ValueError(f"Invalid page dimensions on page {page_index + 1}")
                page_aspects.append(width / height)
                text = text_page.get_text_range()
                section_markers.extend(
                    _section_markers_on_page(
                        text,
                        text_page,
                        page_bbox,
                        page_index + 1,
                    )
                )
                for match in COMPACT_HEADING_PATTERN.finditer(text):
                    number = int(match.group(1))
                    bbox = _match_bbox(
                        text_page, match.start(1), match.end(1), page_bbox
                    )
                    if bbox is None:
                        continue
                    if number in expected_numbers:
                        compact_raw.append((number, page_index + 1, bbox))
                for match in NUMBERED_HEADING_PATTERN.finditer(text):
                    number = int(match.group(1))
                    if number not in expected_numbers:
                        continue
                    bbox = _match_bbox(
                        text_page, match.start(1), match.end(1) + 1, page_bbox
                    )
                    if bbox is None:
                        continue
                    following = text[match.end() : match.end() + 32].lstrip()
                    if following.startswith(CIRCLED_CHOICES):
                        continue
                    generic.append(
                        (number, page_index + 1, bbox[0], bbox[1], following)
                    )
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()

    if sequence_ordinal is not None:
        layout = _generic_layout_for(generic, page_aspects)
        anchors = _select_ordinal_generic_sequence(
            _exclude_horizontal_answer_grid_candidates(generic),
            layout,
            expected_numbers=expected_numbers,
            ordinal=sequence_ordinal,
        )
        if not _is_complete_sequence(anchors, expected_numbers):
            raise ValueError(
                "Could not recover the requested complete answer-heading sequence "
                f"#{sequence_ordinal} from {source.name}"
            )
        return layout, anchors, page_count

    compact_layout = _compact_layout_for(compact_raw)
    if subject_section:
        start, end = _section_window(
            section_markers, subject_section, compact_layout
        )
        compact_raw = [
            item
            for item in compact_raw
            if _position_in_section(
                item[1], item[2][0], item[2][1], start, end, compact_layout
            )
        ]
    compact = [
        AnswerAnchor(number, page, column, bbox)
        for number, page, bbox in compact_raw
        if (column := compact_layout.column_for_x(bbox[0])) is not None
    ]
    compact = sorted(compact, key=lambda anchor: anchor.position)
    if _is_complete_sequence(compact, expected_numbers):
        return compact_layout, compact, page_count

    layout = _generic_layout_for(generic, page_aspects)
    if subject_section:
        start, end = _section_window(section_markers, subject_section, layout)
        generic = [
            item
            for item in generic
            if _position_in_section(item[1], item[2], item[3], start, end, layout)
        ]
    anchors = _select_generic_sequence(
        _exclude_horizontal_answer_grid_candidates(generic),
        layout,
        expected_numbers=expected_numbers,
    )
    if not _is_complete_sequence(anchors, expected_numbers):
        raise ValueError(
            "Could not recover a complete "
            f"1..{expected_count} answer-heading sequence from {source.name}"
        )
    return layout, anchors, page_count


def _generic_layout_for(
    candidates: Iterable[tuple[int, int, float, float, str]],
    page_aspects: Iterable[float],
) -> AnswerLayout:
    if any(aspect > 1.25 for aspect in page_aspects):
        return LANDSCAPE_SPREAD
    positions = [x for _number, _page, x, _y, following in candidates if "출제의도" in following]
    left = sum(x < 0.30 for x in positions)
    middle = sum(0.30 <= x < 0.55 for x in positions)
    right = sum(x >= 0.55 for x in positions)
    if left >= 3 and middle >= 3 and right >= 3:
        return COMPACT_WIDE_THREE_COLUMN
    return DETAILED_SINGLE_COLUMN


def _select_ordinal_generic_sequence(
    candidates: Iterable[tuple[int, int, float, float, str]],
    layout: AnswerLayout,
    *,
    expected_numbers: Iterable[int],
    ordinal: int,
) -> list[AnswerAnchor]:
    """Select one full sequence from a title-less shared answer booklet."""

    expected = list(expected_numbers)
    filtered = [
        item
        for item in candidates
        if "출제의도" in item[4]
        and layout.column_for_x(item[2]) is not None
    ]
    filtered.sort(
        key=lambda item: (item[1], int(layout.column_for_x(item[2]) or 0), item[3])
    )
    sequences: list[list[AnswerAnchor]] = []
    for start_index, item in enumerate(filtered):
        if item[0] != 1:
            continue
        selected: list[AnswerAnchor] = []
        expected_index = 0
        for number, page, x, y, _following in filtered[start_index:]:
            if number == expected[expected_index]:
                column = layout.column_for_x(x)
                assert column is not None
                selected.append(
                    AnswerAnchor(number, page, column, (x, y, x + 0.01, y + 0.01))
                )
                expected_index += 1
                if expected_index == len(expected):
                    sequences.append(selected)
                    break
            elif number == 1 and expected_index:
                break
    if len(sequences) < ordinal:
        return []
    return sequences[ordinal - 1]


def _canonical_section_name(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).replace("Ⅰ", "I").replace("Ⅱ", "II")
    normalized = normalized.upper()
    if normalized.startswith("생물"):
        normalized = "생명과학" + normalized[len("생물") :]
    return normalized


def _section_markers_on_page(
    text: str,
    text_page: Any,
    page_bbox: tuple[float, float, float, float],
    page: int,
) -> list[SectionMarker]:
    markers: list[SectionMarker] = []
    for match in SECTION_MARKER_PATTERN.finditer(text):
        bbox = _match_bbox(text_page, match.start(), match.end(), page_bbox)
        if bbox is None:
            continue
        label = f"{match.group('subject')}{match.group('level')}"
        markers.append(SectionMarker(_canonical_section_name(label), page, bbox))
    return markers


def _section_window(
    markers: Iterable[SectionMarker],
    subject_section: str,
    layout: AnswerLayout,
) -> tuple[SectionMarker, SectionMarker | None]:
    ordered = sorted(markers, key=lambda marker: _marker_position(marker, layout))
    target = _canonical_section_name(subject_section)
    for index, marker in enumerate(ordered):
        if marker.subject == target:
            return marker, ordered[index + 1] if index + 1 < len(ordered) else None
    raise ValueError(f"Could not find answer section {subject_section!r}")


def _position_in_section(
    page: int,
    x: float,
    y: float,
    start: SectionMarker,
    end: SectionMarker | None,
    layout: AnswerLayout,
) -> bool:
    position = _logical_position(page, x, y, layout)
    if position < _marker_position(start, layout):
        return False
    return end is None or position < _marker_position(end, layout)


def _marker_position(marker: SectionMarker, layout: AnswerLayout) -> tuple[int, int, float]:
    center_x = (marker.bbox[0] + marker.bbox[2]) / 2
    return _logical_position(marker.page, center_x, marker.bbox[1], layout)


def _logical_position(
    page: int, x: float, y: float, layout: AnswerLayout
) -> tuple[int, int, float]:
    column = layout.column_for_x(x)
    if column is None:
        raise ValueError("Could not place answer content in its page layout")
    return page, column, y


def _section_end_anchor(
    source_pdf: Path,
    subject_section: str,
    layout: AnswerLayout,
) -> AnswerAnchor | None:
    """Use the next subject title as Q20's safe trailing boundary."""

    document = pdfium.PdfDocument(str(source_pdf))
    try:
        markers: list[SectionMarker] = []
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                markers.extend(
                    _section_markers_on_page(
                        text_page.get_text_range(),
                        text_page,
                        tuple(float(value) for value in page.get_bbox()),
                        page_index + 1,
                    )
                )
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()
    _start, end = _section_window(markers, subject_section, layout)
    if end is None:
        return None
    center_x = (end.bbox[0] + end.bbox[2]) / 2
    column = layout.column_for_x(center_x)
    if column is None:
        raise ValueError(
            f"Could not place next answer section boundary for {subject_section!r}"
        )
    # Shared answer booklets draw the next subject's capsule title with a
    # horizontal rule that begins slightly above the title glyph. Leave a
    # larger guard band than an ordinary question-to-question boundary so the
    # previous subject's Q20 does not retain that rule.
    x0, y0, x1, y1 = end.bbox
    guarded_bbox = (x0, max(0.0, y0 - 0.012), x1, y1)
    return AnswerAnchor(0, end.page, column, guarded_bbox)


def _compact_layout_for(
    compact_raw: list[tuple[int, int, tuple[float, float, float, float]]],
) -> AnswerLayout:
    """Choose between the legacy and denser three-column answer sheets."""

    positions = [bbox[0] for _number, _page, bbox in compact_raw]
    has_two_column_right = sum(0.46 <= x <= 0.56 for x in positions) >= 3
    has_third_column = any(x >= 0.60 for x in positions)
    if has_two_column_right and not has_third_column:
        return COMPACT_TWO_COLUMN

    middle_x = [x for x in positions if 0.25 < x < 0.50]
    if middle_x and statistics.median(middle_x) < 0.34:
        return COMPACT_DENSE_THREE_COLUMN
    if middle_x and statistics.median(middle_x) > 0.365:
        return COMPACT_WIDE_THREE_COLUMN
    return COMPACT_THREE_COLUMN


def _select_generic_sequence(
    candidates: Iterable[tuple[int, int, float, float, str]],
    layout: AnswerLayout,
    *,
    expected_numbers: Iterable[int] = range(1, 21),
) -> list[AnswerAnchor]:
    expected_sequence = list(expected_numbers)
    if not expected_sequence:
        return []
    selected: list[AnswerAnchor] = []
    expected_index = 0
    for number, page, x, y, _following in candidates:
        column = layout.column_for_x(x)
        if column is None or number != expected_sequence[expected_index]:
            continue
        selected.append(
            AnswerAnchor(number, page, column, (x, y, x + 0.001, y + 0.001))
        )
        expected_index += 1
        if expected_index == len(expected_sequence):
            break
    return selected


def _exclude_horizontal_answer_grid_candidates(
    candidates: Iterable[tuple[int, int, float, float, str]],
) -> list[tuple[int, int, float, float, str]]:
    """Discard numbered answer-key rows before selecting explanation headings."""

    items = list(candidates)
    excluded: set[int] = set()
    for index, (_number, page, x, y, _following) in enumerate(items):
        row = [
            other_index
            for other_index, (_other_number, other_page, other_x, other_y, _text) in enumerate(items)
            if other_page == page and abs(other_y - y) <= 0.01
            and abs(other_x - x) >= 0.015
        ]
        row_with_self = [index, *row]
        distinct_numbers = {items[item][0] for item in row_with_self}
        x_positions = [items[item][2] for item in row_with_self]
        if len(distinct_numbers) >= 3 and max(x_positions) - min(x_positions) >= 0.12:
            excluded.update(row_with_self)
    return [item for index, item in enumerate(items) if index not in excluded]


def _is_complete_sequence(
    anchors: list[AnswerAnchor],
    expected_numbers: Iterable[int] = range(1, 21),
) -> bool:
    return [anchor.number for anchor in anchors] == list(expected_numbers)


def parse_answer_key_text(text: str, expected_count: int) -> dict[int, str]:
    """Extract a complete objective answer sequence from PDF text.

    A sequential answer-table run is preferred.  If the PDF text layer emits
    columns out of order, a fallback is accepted only when every expected
    number has one unambiguous circled choice.
    """

    if expected_count <= 0:
        raise ValueError("expected_count must be positive")
    matches = [
        (int(match.group(1)), match.group(2))
        for match in ANSWER_ENTRY_PATTERN.finditer(text)
        if 1 <= int(match.group(1)) <= expected_count
    ]

    for start_index, (number, _choice) in enumerate(matches):
        if number != 1:
            continue
        answer_key: dict[int, str] = {}
        expected = 1
        for candidate_number, choice in matches[start_index:]:
            if candidate_number == expected:
                answer_key[candidate_number] = choice
                expected += 1
                if expected > expected_count:
                    return answer_key
            elif candidate_number == 1 and expected > 1:
                break

    choices_by_number: dict[int, set[str]] = {
        number: set() for number in range(1, expected_count + 1)
    }
    for number, choice in matches:
        choices_by_number[number].add(choice)
    ambiguous = [
        number for number, choices in choices_by_number.items() if len(choices) != 1
    ]
    if ambiguous:
        raise ValueError(
            "Could not extract an unambiguous complete answer key; "
            f"review numbers: {ambiguous}"
        )
    return {
        number: next(iter(choices_by_number[number]))
        for number in range(1, expected_count + 1)
    }


def extract_answer_key(
    source_pdf: str | Path,
    *,
    expected_count: int,
) -> dict[int, str]:
    """Read a complete objective answer table from a local solution PDF."""

    source = Path(source_pdf).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    document = pdfium.PdfDocument(str(source))
    page_text: list[str] = []
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                page_text.append(text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()
    return parse_answer_key_text("\n".join(page_text), expected_count)


def _document_content_end_region(
    source_pdf: Path,
    layout: AnswerLayout,
    page_count: int,
) -> int:
    """Find the final populated logical column, excluding footer page numbers."""

    counts: dict[int, int] = {}
    document = pdfium.PdfDocument(str(source_pdf))
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                page_bbox = tuple(float(value) for value in page.get_bbox())
                page_left, page_bottom, page_right, page_top = page_bbox
                width = page_right - page_left
                height = page_top - page_bottom
                if width <= 0 or height <= 0:
                    continue
                text = text_page.get_text_range()
                for index, char in enumerate(text):
                    if char.isspace():
                        continue
                    left, bottom, right, top = text_page.get_charbox(index)
                    if right <= left or top <= bottom:
                        continue
                    x = ((left + right) / 2 - page_left) / width
                    y = (page_top - top) / height
                    column = layout.column_for_x(x)
                    # Page titles such as "정답 및 해설" are often centered
                    # over otherwise blank continuation pages.  They must not
                    # make an empty later column appear to be Q20 content.
                    content_start = max(0.12, layout.content_top)
                    if column is None or not content_start <= y <= layout.content_bottom:
                        continue
                    region = page_index * layout.column_count + column
                    counts[region] = counts.get(region, 0) + 1
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()

    populated = [region for region, count in counts.items() if count >= 12]
    if populated:
        return max(populated)
    return page_count * layout.column_count - 1


def _match_bbox(
    text_page: Any,
    start: int,
    end: int,
    page_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    boxes: list[tuple[float, float, float, float]] = []
    for index in range(start, end):
        left, bottom, right, top = text_page.get_charbox(index)
        if right > left and top > bottom:
            boxes.append((left, bottom, right, top))
    if not boxes:
        return None
    page_left, page_bottom, page_right, page_top = page_bbox
    width = page_right - page_left
    height = page_top - page_bottom
    if width <= 0 or height <= 0:
        return None
    return (
        round((min(box[0] for box in boxes) - page_left) / width, 6),
        round((page_top - max(box[3] for box in boxes)) / height, 6),
        round((max(box[2] for box in boxes) - page_left) / width, 6),
        round((page_top - min(box[1] for box in boxes)) / height, 6),
    )


def _append_answer_span(
    fragments: list[dict[str, Any]],
    *,
    prefix: str,
    start: AnswerAnchor,
    end: AnswerAnchor | None,
    layout: AnswerLayout,
    page_count: int,
    document_end_region: int,
) -> list[str]:
    start_region = (start.page - 1) * layout.column_count + start.column
    end_region = (
        (end.page - 1) * layout.column_count + end.column
        if end is not None
        else document_end_region
    )
    if end_region < start_region:
        return []
    fragment_ids: list[str] = []
    for sequence, region in enumerate(range(start_region, end_region + 1), start=1):
        page = region // layout.column_count + 1
        column = region % layout.column_count
        x0, x1 = layout.columns[column]
        y0 = layout.content_top
        y1 = layout.content_bottom
        if region == start_region:
            start_floor = layout.start_content_top or layout.content_top
            y0 = max(start_floor, start.bbox[1] - layout.start_margin)
        if end is not None and region == end_region:
            y1 = min(layout.content_bottom, end.bbox[1] - layout.end_margin)
            # A question ending at the bottom of one compact column can be
            # followed by a header-only sliver before the next column's first
            # real heading.  Do not assemble that page header as continuation.
            if region != start_region and y1 - y0 < 0.04:
                continue
        if y1 - y0 < 0.01:
            continue
        fragment: dict[str, Any] = {
            "id": f"{prefix}_part{sequence:02d}",
            "kind": "question",
            "page": page,
            "bbox": [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)],
            "proposal_confidence": "high",
        }
        if layout.column_count == 1:
            fragment["edge_check_sides"] = ["left", "right"]
        elif column == 0:
            fragment["edge_check_sides"] = ["left"]
        elif column == layout.column_count - 1:
            fragment["edge_check_sides"] = ["right"]
        else:
            fragment["edge_check_sides"] = []
        if region == start_region:
            fragment["start_anchor_y"] = round(start.bbox[1], 6)
        # Intermediate answer pages can end mid-explanation.  Their lower
        # body must never be mistaken for a question-paper confirmation
        # footer.  Only the final populated region of Q20 receives that trim.
        if end is None and region == document_end_region:
            fragment["trim_trailing_context"] = True
        fragments.append(fragment)
        fragment_ids.append(fragment["id"])
    return fragment_ids
