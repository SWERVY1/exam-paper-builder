from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .anchors import detect_structure_anchors


def audit_anchor_directory(
    source_dir: str | Path,
    limit: int | None = None,
) -> dict[str, Any]:
    root = Path(source_dir).resolve()
    files = sorted(root.rglob("*.pdf"), key=lambda path: str(path).casefold())
    if limit is not None:
        files = files[:limit]

    records: list[dict[str, Any]] = []
    for path in files:
        record: dict[str, Any] = {
            "path": str(path.relative_to(root)).replace("\\", "/"),
        }
        try:
            result = detect_structure_anchors(path, include_hash=False)
            summary = result["summary"]
            question_count = int(summary["question_anchor_count"])
            gaps = [
                event
                for event in summary["sequence_events"]
                if event["type"] == "number_gap"
            ]
            resets = [
                event
                for event in summary["sequence_events"]
                if event["type"] == "section_reset"
            ]
            sampled_text = any(
                page["text_layer_char_count"] > 0 for page in result["pages"]
            )
            status = classify_anchor_result(
                page_count=result["page_count"],
                question_count=question_count,
                has_text=sampled_text,
                number_gap_count=len(gaps),
                has_structure_ocr=bool(summary.get("ocr_fallback_used")),
            )
            record.update(
                {
                    "status": status,
                    "page_count": result["page_count"],
                    "question_anchor_count": question_count,
                    "group_anchor_count": summary["group_anchor_count"],
                    "rejected_misaligned_question_candidates": summary[
                        "rejected_misaligned_question_candidates"
                    ],
                    "number_gap_count": len(gaps),
                    "section_reset_count": len(resets),
                    "sequence_events": summary["sequence_events"],
                    "ocr_fallback_used": bool(summary.get("ocr_fallback_used")),
                }
            )
        except Exception as exc:
            record.update(
                {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        records.append(record)

    statuses: dict[str, int] = {}
    for record in records:
        status = record["status"]
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(root),
        "file_count": len(records),
        "status_counts": statuses,
        "files": records,
        "raw_body_text_stored": False,
    }


def classify_anchor_result(
    page_count: int,
    question_count: int,
    has_text: bool,
    number_gap_count: int,
    has_structure_ocr: bool = False,
) -> str:
    if (not has_text and not has_structure_ocr) or question_count == 0:
        return "needs_structure_ocr"
    if has_structure_ocr:
        return "needs_anchor_review"
    if number_gap_count > 0:
        return "needs_anchor_review"
    if question_count < 15:
        return "needs_anchor_review"
    if page_count == 4 and question_count != 20:
        return "needs_anchor_review"
    return "proposal_candidate"


def write_anchor_audit(path: str | Path, audit: dict[str, Any]) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination
