from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .anchors import anchor_position, detect_structure_anchors
from .manifest import validate_manifest


@dataclass(frozen=True)
class TwoColumnProfile:
    left_x0: float = 0.055
    left_x1: float = 0.495
    right_x0: float = 0.505
    right_x1: float = 0.945
    content_top: float = 0.142
    # Some question sheets place the ㄱ/ㄴ/ㄷ statement box and the second
    # row of ①~⑤ choices below the former 90.5% page cutoff.  Include that
    # lower region; the page-footer trimmer removes only verified footer ink.
    content_bottom: float = 0.965
    last_content_bottom: float = 0.965
    start_content_top: float = 0.09
    start_margin: float = 0.035
    # Keep only the blank lead-in directly above the next question number.
    # Choice rows (especially a 4/5 second row) often extend farther down than
    # the former 3.5% reserve, so every subject now retains that lower band.
    end_margin: float = 0.002
    # With the smaller lower margin, a page-header-only sliver can reach 10%
    # of a page before the first question anchor.  Treat up to 12% as header
    # context so it is not appended to the preceding question.
    header_sliver_height: float = 0.12

    def x_bounds(self, column: str) -> tuple[float, float]:
        if column == "left":
            return self.left_x0, self.left_x1
        return self.right_x0, self.right_x1


def propose_manifest(
    source_pdf: str | Path,
    output_manifest: str | Path,
    exam_id: str,
    dpi: int = 300,
    output_width_px: int = 1080,
    output_format: str = "webp",
    store_fragments: bool = False,
    profile: TwoColumnProfile | None = None,
    expected_question_count: int | None = None,
) -> dict[str, Any]:
    output_path = Path(output_manifest).resolve()
    detection = detect_structure_anchors(
        source_pdf,
        include_hash=False,
        expected_question_count=expected_question_count,
    )
    profile = profile or _profile_for_source(source_pdf, detection)
    anchors = detection["anchors"]
    questions = [item for item in anchors if item["type"] == "question_start"]
    groups = _trusted_groups(
        [item for item in anchors if item["type"] == "group_start"]
    )
    if not questions:
        raise ValueError("No question anchors were found in the PDF text layer.")

    _assign_sections(questions)
    fragments: list[dict[str, Any]] = []
    stimuli: list[dict[str, Any]] = []
    question_records: list[dict[str, Any]] = []
    warnings: list[str] = []
    document_end_region = _document_end_region(detection, questions)

    group_links: dict[int, list[str]] = {id(question): [] for question in questions}
    valid_groups: list[dict[str, Any]] = []
    for group_index, group in enumerate(groups, start=1):
        following = [
            question
            for question in questions
            if anchor_position(question) > anchor_position(group)
            and int(question["number"]) == int(group["range_start"])
        ]
        if not following:
            warnings.append(
                f"No first question found for range {group['range_start']}-{group['range_end']} "
                f"on page {group['page']}."
            )
            continue
        valid_groups.append(group)
        first_question = following[0]
        section = int(first_question["section_index"])
        stimulus_id = (
            f"SEC{section:02d}_S{int(group['range_start']):03d}_"
            f"{int(group['range_end']):03d}_{group_index:02d}"
        )
        group_fragment_ids = _append_span_fragments(
            fragments,
            prefix=stimulus_id.lower(),
            kind="stimulus",
            start=group,
            end=first_question,
            profile=profile,
            document_end_region=document_end_region,
        )
        if not group_fragment_ids:
            warnings.append(f"Range {stimulus_id} produced no crop fragments.")
            continue
        stimuli.append({"id": stimulus_id, "fragment_ids": group_fragment_ids})
        for question in questions:
            if int(question["section_index"]) != section:
                continue
            if not (int(group["range_start"]) <= int(question["number"]) <= int(group["range_end"])):
                continue
            if anchor_position(question) <= anchor_position(group):
                continue
            group_links[id(question)].append(stimulus_id)

    all_boundaries = sorted([*questions, *valid_groups], key=anchor_position)
    for index, question in enumerate(questions):
        next_boundaries = [
            boundary
            for boundary in all_boundaries
            if anchor_position(boundary) > anchor_position(question)
        ]
        end = next_boundaries[0] if next_boundaries else None
        section = int(question["section_index"])
        question_id = f"SEC{section:02d}_Q{int(question['number']):03d}"
        question_fragment_ids = _append_span_fragments(
            fragments,
            prefix=question_id.lower(),
            kind="question",
            start=question,
            end=end,
            profile=profile,
            document_end_region=document_end_region,
        )
        if not question_fragment_ids:
            warnings.append(f"Question {question_id} produced no crop fragments.")
            continue
        question_records.append(
            {
                "id": question_id,
                "section": f"section_{section:02d}",
                "number": int(question["number"]),
                "stimulus_ids": group_links[id(question)],
                "fragment_ids": question_fragment_ids,
            }
        )

    relative_source = os.path.relpath(Path(source_pdf).resolve(), output_path.parent)
    manifest: dict[str, Any] = {
        "version": 1,
        "exam_id": exam_id,
        "source_pdf": relative_source.replace("\\", "/"),
        "review_status": "needs_review",
        "proposal": {
            "profile": "two_column_legacy_korean",
            "question_anchor_count": detection["summary"]["question_anchor_count"],
            "group_anchor_count": detection["summary"]["group_anchor_count"],
            "sequence_events": detection["summary"]["sequence_events"],
            "warnings": warnings,
            "body_text_stored": False,
        },
        "render": {
            "dpi": dpi,
            "output_width_px": output_width_px,
            "padding_px": 60,
            "gap_px": 36,
            "background": "#FFFFFF",
            "max_output_height_px": 16000 if output_format == "webp" else 24000,
            "min_ink_ratio": 0.002,
            "output_format": output_format,
            "store_fragments": store_fragments,
        },
        "fragments": fragments,
        "stimuli": stimuli,
        "questions": question_records,
    }
    validate_manifest(manifest, page_count=detection["page_count"])
    return manifest


def _assign_sections(questions: list[dict[str, Any]]) -> None:
    section = 1
    previous_number: int | None = None
    used_in_section: set[int] = set()
    for question in questions:
        number = int(question["number"])
        if previous_number is not None and (
            number <= previous_number or number in used_in_section
        ):
            section += 1
            used_in_section.clear()
        question["section_index"] = section
        used_in_section.add(number)
        previous_number = number


def _append_span_fragments(
    fragments: list[dict[str, Any]],
    prefix: str,
    kind: str,
    start: dict[str, Any],
    end: dict[str, Any] | None,
    profile: TwoColumnProfile,
    document_end_region: int | None = None,
) -> list[str]:
    start_region = _region_index(start)
    end_region = (
        _region_index(end)
        if end is not None
        else document_end_region
        if document_end_region is not None
        else start_region
    )
    if end_region < start_region:
        return []
    fragment_ids: list[str] = []
    for sequence, region in enumerate(range(start_region, end_region + 1), start=1):
        page = region // 2 + 1
        column = "left" if region % 2 == 0 else "right"
        x0, x1 = profile.x_bounds(column)
        y0 = profile.content_top
        y1 = profile.content_bottom
        if region == start_region:
            y0 = max(profile.start_content_top, _effective_start_y(start, profile))
        if end is not None and region == end_region:
            y1 = min(
                profile.content_bottom,
                float(end["bbox"][1]) - profile.end_margin,
            )
        elif end is None and region == end_region:
            y1 = min(y1, profile.last_content_bottom)
        if (
            region != start_region
            and region == end_region
            and y1 - profile.content_top <= profile.header_sliver_height
        ):
            # When the next question begins directly below a page header, the
            # narrow pre-anchor strip contains only the repeated header.  Do
            # not append that header to the previous question image.
            continue
        if y1 - y0 < 0.01:
            continue
        fragment_id = f"{prefix}_part{sequence:02d}"
        fragment = {
            "id": fragment_id,
            "kind": kind,
            "page": page,
            "bbox": [
                round(x0, 6),
                round(y0, 6),
                round(x1, 6),
                round(y1, 6),
            ],
            "proposal_confidence": start.get("confidence", "unrated"),
        }
        if region == start_region:
            fragment["start_anchor_y"] = round(float(start["bbox"][1]), 6)
        if end is None and region == end_region:
            # Footer removal is safe only for the physical final fragment of
            # the document.  A question that continues into a later column or
            # page can legitimately contain a blank band before its 보기 box
            # and ①~⑤ choices; treating that band as a footer loses the rest
            # of the question (notably ㄱ/ㄴ/ㄷ items near a page bottom).
            fragment["trim_trailing_context"] = True
        fragments.append(fragment)
        fragment_ids.append(fragment_id)
    return fragment_ids


def _region_index(anchor: dict[str, Any] | None) -> int:
    if anchor is None:
        raise ValueError("Anchor is required for region indexing.")
    return (int(anchor["page"]) - 1) * 2 + (0 if anchor["column"] == "left" else 1)


def _effective_start_y(anchor: dict[str, Any], profile: TwoColumnProfile) -> float:
    margin = profile.start_margin
    if anchor.get("type") == "group_start" and anchor.get("style") == "number_pair":
        margin = max(margin, 0.04)
    return float(anchor["bbox"][1]) - margin


def _document_end_region(
    detection: dict[str, Any], questions: list[dict[str, Any]]
) -> int:
    """Exclude a textless trailing OMR page, but preserve normal continuations."""

    default_end = int(detection["page_count"]) * 2 - 1
    if not questions:
        return default_end

    last_question_page = max(int(question["page"]) for question in questions)
    trailing_pages = [
        page
        for page in detection.get("pages", [])
        if int(page.get("page", 0)) > last_question_page
    ]
    if trailing_pages and all(
        int(page.get("text_layer_char_count", 0)) == 0
        for page in trailing_pages
    ):
        # Keep both columns on the final question page. A subsequent blank
        # page in these scans is an OMR sheet, not a continuation fragment.
        return last_question_page * 2 - 1
    return default_end


def _trusted_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep explicit ranges and nested pair ranges, rejecting table-number pairs."""

    explicit = [group for group in groups if group.get("style") == "hyphen"]
    trusted = list(explicit)
    for group in groups:
        if group.get("style") != "number_pair":
            continue
        nested = any(
            int(parent["range_start"]) <= int(group["range_start"])
            and int(group["range_end"]) <= int(parent["range_end"])
            and anchor_position(parent) < anchor_position(group)
            for parent in explicit
        )
        if nested:
            trusted.append(group)
    return sorted(trusted, key=anchor_position)


def _profile_for_detection(detection: dict[str, Any]) -> TwoColumnProfile:
    """Choose the safe content bottom for full-page versus inset CropBox PDFs."""

    profile = TwoColumnProfile()
    inset = any(
        abs(float(page.get("page_bbox_points", [0, 0, 0, 0])[0])) > 1.0
        or abs(float(page.get("page_bbox_points", [0, 0, 0, 0])[1])) > 1.0
        for page in detection.get("pages", [])
    )
    if inset:
        return replace(profile, content_bottom=0.93, last_content_bottom=0.93)
    return profile


def _profile_for_source(
    source_pdf: str | Path, detection: dict[str, Any]
) -> TwoColumnProfile:
    """Choose a printable column while excluding a verified modern subject ribbon."""

    profile = _profile_for_detection(detection)
    source_name = Path(source_pdf).name
    year_match = re.match(r"^(20\d{2})", source_name)
    year = int(year_match.group(1)) if year_match else None
    is_science_ii = "화학Ⅱ" in source_name or "생명과학Ⅱ" in source_name
    if is_science_ii and year is not None and year >= 2019:
        # Newer Science II sheets reserve the outer page edge for a vertical
        # subject ribbon.  It is document furniture, not question content.
        # Earlier layouts remain wider because their tables and choices can
        # legitimately use that outer printable space.
        return replace(profile, right_x1=0.91)
    return profile
