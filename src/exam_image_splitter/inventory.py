from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from .build import sha256_file


def inventory_directory(
    source_dir: str | Path,
    include_hashes: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    root = Path(source_dir).resolve()
    files = sorted(root.rglob("*.pdf"), key=lambda path: str(path).casefold())
    if limit is not None:
        files = files[:limit]
    records: list[dict[str, Any]] = []
    hash_paths: dict[str, list[str]] = {}
    total_pages = 0
    total_bytes = 0

    for path in files:
        relative = str(path.relative_to(root)).replace("\\", "/")
        record: dict[str, Any] = {
            "path": relative,
            "bytes": path.stat().st_size,
            "status": "ok",
        }
        total_bytes += path.stat().st_size
        try:
            document = pdfium.PdfDocument(str(path))
            try:
                page_count = len(document)
                total_pages += page_count
                record["page_count"] = page_count
                sample_indices = sorted({0, page_count // 2, page_count - 1}) if page_count else []
                sampled_chars: list[int] = []
                sizes: list[list[float]] = []
                for page_index in sample_indices:
                    page = document[page_index]
                    text_page = page.get_textpage()
                    try:
                        width, height = page.get_size()
                        sizes.append([round(float(width), 2), round(float(height), 2)])
                        sampled_chars.append(text_page.count_chars())
                    finally:
                        text_page.close()
                        page.close()
                record["sample_page_sizes_points"] = sizes
                record["sample_text_layer_char_counts"] = sampled_chars
                record["has_sample_text_layer"] = any(count > 0 for count in sampled_chars)
            finally:
                document.close()
            if include_hashes:
                digest = sha256_file(path)
                record["sha256"] = digest
                hash_paths.setdefault(digest, []).append(relative)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)

    duplicate_groups = [
        {"sha256": digest, "paths": paths}
        for digest, paths in hash_paths.items()
        if len(paths) > 1
    ]
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(root),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "total_pages": total_pages,
        "error_count": sum(record["status"] == "error" for record in records),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "files": records,
        "body_text_extracted": False,
    }


def write_inventory(path: str | Path, inventory: dict[str, Any]) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination

