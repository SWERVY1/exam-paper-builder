from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any, Iterable

import pypdfium2 as pdfium

from .build import sha256_file
from .ocr_fallback import WindowsOcrUnavailable, detect_question_anchors_with_windows_ocr


QUESTION_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*[.]")
RANGE_PATTERN = re.compile(
    r"(?<!\d)[\[\(【]?\s*(\d{1,3})\s*[-~]\s*(\d{1,3})\s*[\]\)】]?"
)
PAIR_RANGE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,3})[ \t]+(\d{1,3})[ \t]*[.]"
)


def detect_structure_anchors(
    source_pdf: str | Path,
    include_hash: bool = True,
    expected_question_count: int | None = None,
) -> dict[str, Any]:
    source = Path(source_pdf).resolve()
    document = pdfium.PdfDocument(str(source))
    anchors: list[dict[str, Any]] = []
    page_records: list[dict[str, Any]] = []
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                width, height = page.get_size()
                page_bbox = tuple(float(value) for value in page.get_bbox())
                text = text_page.get_text_range()
                group_matches = _group_matches(text)
                occupied_group_spans = [match[0] for match in group_matches]
                page_anchors: list[dict[str, Any]] = []

                for span, start_number, end_number, style in group_matches:
                    bbox = _normalized_match_bbox(
                        text_page, span.start(), span.end(), page_bbox
                    )
                    if bbox is None:
                        continue
                    anchor = {
                        "type": "group_start",
                        "page": page_index + 1,
                        "column": _column_for_bbox(bbox),
                        "range_start": start_number,
                        "range_end": end_number,
                        "bbox": bbox,
                        "style": style,
                        "confidence": "high" if style == "hyphen" else "medium",
                    }
                    page_anchors.append(anchor)

                for match in QUESTION_PATTERN.finditer(text):
                    number = int(match.group(1))
                    if not 1 <= number <= 100:
                        continue
                    if any(_spans_overlap(match, group_span) for group_span in occupied_group_spans):
                        continue
                    bbox = _normalized_match_bbox(
                        text_page, match.start(), match.end(), page_bbox
                    )
                    if bbox is None or not _plausible_question_x(bbox[0]):
                        continue
                    page_anchors.append(
                        {
                            "type": "question_start",
                            "page": page_index + 1,
                            "column": _column_for_bbox(bbox),
                            "number": number,
                            "bbox": bbox,
                            "confidence": "unrated",
                        }
                    )

                page_anchors = _deduplicate_page_anchors(page_anchors)
                anchors.extend(page_anchors)
                page_records.append(
                    {
                        "page": page_index + 1,
                        "width_points": round(float(width), 3),
                        "height_points": round(float(height), 3),
                        "page_bbox_points": [
                            round(value, 3) for value in page_bbox
                        ],
                        "text_layer_char_count": len(text),
                        "anchor_count": len(page_anchors),
                    }
                )
            finally:
                text_page.close()
                page.close()
    finally:
        page_count = len(document)
        document.close()

    _rate_question_alignment(anchors)
    rejected_question_count = sum(
        item["type"] == "question_start" and item["confidence"] != "high"
        for item in anchors
    )
    anchors = [
        item
        for item in anchors
        if item["type"] != "question_start" or item["confidence"] == "high"
    ]
    questions = [item for item in anchors if item["type"] == "question_start"]
    ocr_fallback_used = False
    if not questions:
        try:
            expected_counts = (
                (int(expected_question_count),)
                if expected_question_count is not None
                else (20, 30)
            )
            ocr_anchors = detect_question_anchors_with_windows_ocr(
                source, expected_counts=expected_counts
            )
        except WindowsOcrUnavailable:
            ocr_anchors = []
        if ocr_anchors:
            anchors.extend(ocr_anchors)
            ocr_fallback_used = True
    for page_record in page_records:
        page_record["anchor_count"] = sum(
            item["page"] == page_record["page"] for item in anchors
        )
    anchors.sort(key=anchor_position)
    questions = [item for item in anchors if item["type"] == "question_start"]
    groups = [item for item in anchors if item["type"] == "group_start"]
    result = {
        "schema_version": 1,
        "source_pdf": str(source),
        "page_count": page_count,
        "pages": page_records,
        "anchors": anchors,
        "summary": {
            "question_anchor_count": len(questions),
            "group_anchor_count": len(groups),
            "rejected_misaligned_question_candidates": rejected_question_count,
            "sequence_events": _question_sequence_events(questions),
            "raw_body_text_stored": False,
            "ocr_fallback_used": ocr_fallback_used,
        },
    }
    if include_hash:
        result["source_sha256"] = sha256_file(source)
    return result


def anchor_position(anchor: dict[str, Any]) -> tuple[int, int, float]:
    return (
        int(anchor["page"]),
        0 if anchor["column"] == "left" else 1,
        float(anchor["bbox"][1]),
    )


def _group_matches(text: str) -> list[tuple[re.Match[str], int, int, str]]:
    matches: list[tuple[re.Match[str], int, int, str]] = []
    occupied: list[re.Match[str]] = []
    for match in RANGE_PATTERN.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        if _plausible_range(start, end):
            matches.append((match, start, end, "hyphen"))
            occupied.append(match)
    for match in PAIR_RANGE_PATTERN.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        if not _plausible_range(start, end):
            continue
        if any(_spans_overlap(match, existing) for existing in occupied):
            continue
        matches.append((match, start, end, "number_pair"))
    return matches


def _plausible_range(start: int, end: int) -> bool:
    return 1 <= start < end <= 100 and end - start <= 20


def _spans_overlap(first: re.Match[str], second: re.Match[str]) -> bool:
    return first.start() < second.end() and second.start() < first.end()


def _normalized_match_bbox(
    text_page: Any,
    start: int,
    end: int,
    page_bbox: tuple[float, float, float, float],
) -> list[float] | None:
    boxes: list[tuple[float, float, float, float]] = []
    for index in range(start, end):
        left, bottom, right, top = text_page.get_charbox(index)
        if right > left and top > bottom:
            boxes.append((left, bottom, right, top))
    if not boxes:
        return None
    page_left, _page_bottom, page_right, page_top = page_bbox
    page_width = page_right - page_left
    page_height = page_top - _page_bottom
    left = (min(box[0] for box in boxes) - page_left) / page_width
    right = (max(box[2] for box in boxes) - page_left) / page_width
    top = (page_top - max(box[3] for box in boxes)) / page_height
    bottom = (page_top - min(box[1] for box in boxes)) / page_height
    values = [left, top, right, bottom]
    return [round(max(0.0, min(1.0, value)), 6) for value in values]


def _column_for_bbox(bbox: list[float]) -> str:
    center = (bbox[0] + bbox[2]) / 2
    return "left" if center < 0.5 else "right"


def _plausible_question_x(x: float) -> bool:
    return 0.03 <= x <= 0.28 or 0.46 <= x <= 0.70


def _deduplicate_page_anchors(
    anchors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for anchor in sorted(anchors, key=anchor_position):
        if anchor["type"] == "group_start" and anchor.get("style") == "number_pair":
            dominated = any(
                other is not anchor
                and other["type"] == "group_start"
                and other.get("style") == "number_pair"
                and other.get("range_start") == anchor.get("range_start")
                and int(other.get("range_end", 0)) > int(anchor.get("range_end", 0))
                and abs(float(other["bbox"][1]) - float(anchor["bbox"][1])) < 0.12
                for other in anchors
            )
            if dominated:
                continue
        duplicate = False
        for existing in result:
            if anchor["type"] != existing["type"]:
                continue
            same_value = (
                anchor.get("number") == existing.get("number")
                and anchor.get("range_start") == existing.get("range_start")
                and anchor.get("range_end") == existing.get("range_end")
            )
            if same_value and abs(anchor["bbox"][1] - existing["bbox"][1]) < 0.01:
                duplicate = True
                break
        if not duplicate:
            result.append(anchor)
    return result


def _rate_question_alignment(anchors: list[dict[str, Any]]) -> None:
    questions = [item for item in anchors if item["type"] == "question_start"]
    for column in ("left", "right"):
        items = [item for item in questions if item["column"] == column]
        if not items:
            continue
        center = statistics.median(item["bbox"][0] for item in items)
        for item in items:
            distance = abs(item["bbox"][0] - center)
            if distance <= 0.015:
                item["confidence"] = "high"
            elif distance <= 0.04:
                item["confidence"] = "medium"
            else:
                item["confidence"] = "low"


def _question_sequence_events(questions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for question in questions:
        if previous is not None:
            expected = int(previous["number"]) + 1
            current = int(question["number"])
            if current <= int(previous["number"]):
                events.append(
                    {
                        "type": "section_reset",
                        "after": previous["number"],
                        "before": current,
                        "page": question["page"],
                    }
                )
            elif current != expected:
                events.append(
                    {
                        "type": "number_gap",
                        "expected": expected,
                        "found": current,
                        "page": question["page"],
                    }
                )
        previous = question
    return events
