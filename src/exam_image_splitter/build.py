from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from .assemble import AssemblyConfig, assemble_vertical
from .manifest import ManifestError, load_manifest, resolve_source_pdf, validate_manifest
from .pdf_render import PdfRenderer


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ink_ratio(image: Image.Image, threshold: int = 245) -> float:
    grayscale = image.convert("L")
    try:
        histogram = grayscale.histogram()
    finally:
        grayscale.close()
    dark_pixels = sum(histogram[:threshold])
    return dark_pixels / (image.width * image.height)


def edge_ink_metrics(
    image: Image.Image,
    side: str,
    *,
    threshold: int = 245,
    band_width_px: int = 2,
) -> dict[str, float]:
    """Measure ink touching a crop edge before assembly padding hides it."""

    if side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    width = max(1, min(int(band_width_px), image.width))
    x0 = 0 if side == "left" else image.width - width
    grayscale = image.convert("L").crop((x0, 0, x0 + width, image.height))
    try:
        if hasattr(grayscale, "get_flattened_data"):
            pixels = list(grayscale.get_flattened_data())
        else:
            pixels = list(grayscale.getdata())
    finally:
        grayscale.close()
    dark_pixels = sum(value < threshold for value in pixels)
    contact_rows = sum(
        any(value < threshold for value in pixels[row * width : (row + 1) * width])
        for row in range(image.height)
    )
    return {
        "ink_ratio": dark_pixels / max(1, width * image.height),
        "contact_row_ratio": contact_rows / max(1, image.height),
    }


def bottom_edge_ink_metrics(
    image: Image.Image,
    *,
    threshold: int = 245,
    band_height_px: int = 2,
) -> dict[str, float]:
    """Measure ink cut by the bottom of a proposed fragment."""

    height = max(1, min(int(band_height_px), image.height))
    grayscale = image.convert("L").crop(
        (0, image.height - height, image.width, image.height)
    )
    try:
        if hasattr(grayscale, "get_flattened_data"):
            pixels = list(grayscale.get_flattened_data())
        else:
            pixels = list(grayscale.getdata())
    finally:
        grayscale.close()
    dark_pixels = sum(value < threshold for value in pixels)
    contact_columns = sum(
        any(
            pixels[row * image.width + column] < threshold
            for row in range(height)
        )
        for column in range(image.width)
    )
    return {
        "ink_ratio": dark_pixels / max(1, image.width * height),
        "contact_column_ratio": contact_columns / max(1, image.width),
    }


def should_warn_bottom_edge(
    fragment: dict[str, Any],
    metrics: dict[str, float],
    *,
    max_ink_ratio: float,
    max_contact_column_ratio: float,
) -> bool:
    """Flag only a page-ending crop; a bounded next-anchor crop is intentional."""

    return (
        bool(fragment.get("trim_trailing_context"))
        and metrics["ink_ratio"] > max_ink_ratio
        and metrics["contact_column_ratio"] > max_contact_column_ratio
    )


def outer_edge_sides(bbox: list[float]) -> tuple[str, ...]:
    """Return the physical page edge(s) represented by a fragment crop."""

    x0, _, x1, _ = bbox
    if x1 <= 0.5:
        return ("left",)
    if x0 >= 0.5:
        return ("right",)
    return ("left", "right")


def build_exam(
    manifest_path: str | Path,
    output_root: str | Path,
    only_question_ids: Iterable[str] | None = None,
    preview_only: bool = False,
) -> Path:
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    review_status = manifest.get("review_status")
    if review_status is not None and review_status != "approved" and not preview_only:
        raise ManifestError(
            "This auto-generated manifest still needs review. "
            "Set review_status to approved only after checking its page crops."
        )
    source_pdf = resolve_source_pdf(manifest_path, manifest["source_pdf"])
    if not source_pdf.is_file():
        raise ManifestError(f"Source PDF not found: {source_pdf}")

    source_hash = sha256_file(source_pdf)
    render_settings = manifest.get("render", {})
    dpi = int(render_settings.get("dpi", 300))
    output_format = str(render_settings.get("output_format", "png"))
    store_fragments = bool(render_settings.get("store_fragments", True))
    crop_images: dict[str, Image.Image] = {}
    crop_records: list[dict[str, Any]] = []
    question_records: list[dict[str, Any]] = []
    warnings: list[str] = []

    with PdfRenderer(source_pdf, dpi=dpi) as renderer:
        page_count = renderer.page_count
        validate_manifest(manifest, page_count=page_count)
        questions = manifest["questions"]
        question_by_id = {question["id"]: question for question in questions}
        selected_ids = list(only_question_ids or question_by_id.keys())
        if len(selected_ids) != len(set(selected_ids)):
            raise ManifestError("Question ids passed to --only must not repeat.")
        unknown = sorted(set(selected_ids) - set(question_by_id))
        if unknown:
            raise ManifestError(f"Unknown question ids requested: {', '.join(unknown)}")
        if not selected_ids:
            raise ManifestError("No questions selected for build.")

        fragment_by_id = {item["id"]: item for item in manifest["fragments"]}
        stimulus_by_id = {item["id"]: item for item in manifest["stimuli"]}
        required_fragment_ids: list[str] = []
        for question_id in selected_ids:
            question = question_by_id[question_id]
            for stimulus_id in question.get("stimulus_ids", []):
                required_fragment_ids.extend(
                    stimulus_by_id[stimulus_id]["fragment_ids"]
                )
            required_fragment_ids.extend(question["fragment_ids"])
        required_set = set(required_fragment_ids)

        exam_dir = Path(output_root).resolve() / manifest["exam_id"]
        fragments_dir = exam_dir / "fragments"
        exports_dir = exam_dir / "exports"
        fragments_dir.mkdir(parents=True, exist_ok=True)
        exports_dir.mkdir(parents=True, exist_ok=True)

        min_ink = float(render_settings.get("min_ink_ratio", 0.002))
        max_edge_ink = float(
            render_settings.get("max_outer_edge_ink_ratio", 0.01)
        )
        max_edge_rows = float(
            render_settings.get("max_outer_edge_contact_row_ratio", 0.015)
        )
        max_bottom_ink = float(
            render_settings.get("max_bottom_edge_ink_ratio", 0.02)
        )
        max_bottom_columns = float(
            render_settings.get("max_bottom_edge_contact_column_ratio", 0.05)
        )
        try:
            for fragment in manifest["fragments"]:
                fragment_id = fragment["id"]
                if fragment_id not in required_set:
                    continue
                raw_crop = renderer.crop(fragment["page"], fragment["bbox"])
                raw_height = raw_crop.height
                bbox_y0 = float(fragment["bbox"][1])
                bbox_y1 = float(fragment["bbox"][3])
                page_height_px = round(
                    raw_height / max(0.000001, bbox_y1 - bbox_y0)
                )
                top_trimmed_px = 0
                working_crop = raw_crop
                if "start_anchor_y" in fragment:
                    anchor_y = float(fragment["start_anchor_y"])
                    anchor_hint_px = round(
                        (anchor_y - bbox_y0)
                        / max(0.000001, bbox_y1 - bbox_y0)
                        * raw_crop.height
                    )
                    working_crop = _trim_leading_context(
                        raw_crop,
                        anchor_hint_px=anchor_hint_px,
                        dpi=dpi,
                    )
                    top_trimmed_px = raw_crop.height - working_crop.height
                    if working_crop is not raw_crop:
                        raw_crop.close()
                trailing_trimmed_px = 0
                if fragment.get("trim_trailing_context"):
                    crop_top_y = bbox_y0 + top_trimmed_px / page_height_px
                    trailing_crop = _trim_trailing_context(
                        working_crop,
                        page_height_px=page_height_px,
                        crop_top_y=crop_top_y,
                        dpi=dpi,
                    )
                    trailing_trimmed_px = working_crop.height - trailing_crop.height
                    if trailing_crop is not working_crop:
                        working_crop.close()
                    working_crop = trailing_crop
                crop_top_y = bbox_y0 + top_trimmed_px / page_height_px
                narrow_footer_crop = _trim_narrow_page_footer(
                    working_crop,
                    page_height_px=page_height_px,
                    crop_top_y=crop_top_y,
                    dpi=dpi,
                )
                narrow_footer_trimmed_px = (
                    working_crop.height - narrow_footer_crop.height
                )
                if narrow_footer_crop is not working_crop:
                    working_crop.close()
                working_crop = narrow_footer_crop
                crop = _trim_blank_bottom(working_crop, dpi=dpi)
                if crop is not working_crop:
                    working_crop.close()
                ratio = ink_ratio(crop)
                outer_edges: dict[str, dict[str, float]] = {}
                configured_edge_sides = fragment.get("edge_check_sides")
                edge_sides = (
                    tuple(configured_edge_sides)
                    if configured_edge_sides is not None
                    else outer_edge_sides(fragment["bbox"])
                )
                for side in edge_sides:
                    metrics = edge_ink_metrics(crop, side)
                    outer_edges[side] = {
                        "ink_ratio": round(metrics["ink_ratio"], 6),
                        "contact_row_ratio": round(
                            metrics["contact_row_ratio"], 6
                        ),
                    }
                bottom_edge = bottom_edge_ink_metrics(crop)
                bottom_edge_record = {
                    "ink_ratio": round(bottom_edge["ink_ratio"], 6),
                    "contact_column_ratio": round(
                        bottom_edge["contact_column_ratio"], 6
                    ),
                }
                fragment_path = fragments_dir / f"{fragment_id}.png"
                if store_fragments:
                    _save_image_atomic(crop, fragment_path, "png")
                crop_images[fragment_id] = crop
                crop_records.append(
                    {
                        "id": fragment_id,
                        "kind": fragment["kind"],
                        "page": fragment["page"],
                        "bbox": fragment["bbox"],
                        "width_px": crop.width,
                        "height_px": crop.height,
                        "height_before_trim_px": raw_height,
                        "top_trimmed_px": top_trimmed_px,
                        "trailing_context_trimmed_px": trailing_trimmed_px,
                        "narrow_footer_trimmed_px": narrow_footer_trimmed_px,
                        "ink_ratio": round(ratio, 6),
                        "outer_edge_ink": outer_edges,
                        "bottom_edge_ink": bottom_edge_record,
                        "path": (
                            _relative_posix(fragment_path, exam_dir)
                            if store_fragments
                            else None
                        ),
                    }
                )
                if ratio < min_ink:
                    warnings.append(
                        f"Fragment {fragment_id} has low ink ratio "
                        f"{ratio:.6f} (< {min_ink:.6f})."
                    )
                for side, metrics in outer_edges.items():
                    if (
                        metrics["ink_ratio"] > max_edge_ink
                        and metrics["contact_row_ratio"] > max_edge_rows
                    ):
                        warnings.append(
                            "OUTER_EDGE_INK: Fragment "
                            f"{fragment_id} touches the {side} page edge "
                            f"(ink={metrics['ink_ratio']:.6f}, "
                            f"rows={metrics['contact_row_ratio']:.6f})."
                        )
                if should_warn_bottom_edge(
                    fragment,
                    bottom_edge_record,
                    max_ink_ratio=max_bottom_ink,
                    max_contact_column_ratio=max_bottom_columns,
                ):
                    warnings.append(
                        "BOTTOM_EDGE_INK: Fragment "
                        f"{fragment_id} touches the bottom crop edge "
                        f"(ink={bottom_edge_record['ink_ratio']:.6f}, "
                        "columns="
                        f"{bottom_edge_record['contact_column_ratio']:.6f})."
                    )

            config = _assembly_config(render_settings)
            for question_id in selected_ids:
                question = question_by_id[question_id]
                ordered_ids: list[str] = []
                for stimulus_id in question.get("stimulus_ids", []):
                    ordered_ids.extend(
                        stimulus_by_id[stimulus_id]["fragment_ids"]
                    )
                ordered_ids.extend(question["fragment_ids"])
                pieces = [crop_images[fragment_id] for fragment_id in ordered_ids]
                assembled_parts = assemble_vertical(pieces, config)
                extension = ".webp" if output_format == "webp" else ".png"
                base_name = question.get("export", {}).get(
                    "filename", f"{question_id}{extension}"
                )
                output_records: list[dict[str, Any]] = []
                for index, part in enumerate(assembled_parts, start=1):
                    if len(assembled_parts) == 1:
                        filename = base_name
                    else:
                        base = Path(base_name)
                        filename = f"{base.stem}_{index:02d}{base.suffix}"
                    export_path = exports_dir / filename
                    _save_image_atomic(part, export_path, output_format)
                    output_records.append(
                        {
                            "path": _relative_posix(export_path, exam_dir),
                            "width_px": part.width,
                            "height_px": part.height,
                        }
                    )
                    part.close()
                question_records.append(
                    {
                        "id": question_id,
                        "section": question["section"],
                        "number": question["number"],
                        "stimulus_ids": question.get("stimulus_ids", []),
                        "fragment_ids": question["fragment_ids"],
                        "assembly_fragment_ids": ordered_ids,
                        "outputs": output_records,
                    }
                )
        finally:
            for image in crop_images.values():
                image.close()

    resolved = copy.deepcopy(manifest)
    resolved["source_pdf"] = str(source_pdf)
    resolved["source_sha256"] = source_hash
    resolved["source_page_count"] = page_count
    _write_json_atomic(exam_dir / "manifest.resolved.json", resolved)
    report = {
        "schema_version": 1,
        "exam_id": manifest["exam_id"],
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_pdf": str(source_pdf),
        "source_sha256": source_hash,
        "source_page_count": page_count,
        "render_dpi": dpi,
        "output_format": output_format,
        "store_fragments": store_fragments,
        "preview_only": preview_only,
        "fragments": crop_records,
        "questions": question_records,
        "warnings": warnings,
        "body_text_ocr": False,
    }
    report_path = exam_dir / "build-report.json"
    _write_json_atomic(report_path, report)
    return report_path


def _assembly_config(render: dict[str, Any]) -> AssemblyConfig:
    return AssemblyConfig(
        output_width_px=int(render.get("output_width_px", 1800)),
        padding_px=int(render.get("padding_px", 60)),
        gap_px=int(render.get("gap_px", 36)),
        background=str(render.get("background", "#FFFFFF")),
        max_output_height_px=int(render.get("max_output_height_px", 24000)),
    )


def _save_image_atomic(image: Image.Image, path: Path, output_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if output_format == "webp":
        image.save(
            temporary,
            format="WEBP",
            lossless=True,
            quality=100,
            method=4,
            exact=True,
        )
    else:
        image.save(temporary, format="PNG", optimize=False)
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def _relative_posix(path: Path, base: Path) -> str:
    return str(path.relative_to(base)).replace("\\", "/")


def _trim_blank_bottom(
    image: Image.Image,
    dpi: int,
    threshold: int = 245,
) -> Image.Image:
    sample_width = min(256, image.width)
    grayscale = image.convert("L").resize(
        (sample_width, image.height), Image.Resampling.BOX
    )
    try:
        if hasattr(grayscale, "get_flattened_data"):
            pixels = list(grayscale.get_flattened_data())
        else:
            pixels = list(grayscale.getdata())
    finally:
        grayscale.close()

    last_ink_row = -1
    for row in range(image.height - 1, -1, -1):
        start = row * sample_width
        if any(value < threshold for value in pixels[start : start + sample_width]):
            last_ink_row = row
            break
    if last_ink_row < 0:
        return image
    padding = max(12, round(dpi * 0.08))
    bottom = min(image.height, last_ink_row + 1 + padding)
    if bottom >= image.height - 2:
        return image
    return image.crop((0, 0, image.width, bottom))


def _trim_leading_context(
    image: Image.Image,
    anchor_hint_px: int,
    dpi: int,
    threshold: int = 245,
) -> Image.Image:
    """Remove a header or prior-column tail before a proposed start anchor.

    The proposal deliberately includes a generous top margin so that tall
    question numbers and formulae are never clipped.  If that margin also
    captures prior content, the last substantial blank band before the anchor
    is a safe visual separator.  No OCR or body text is used.
    """

    if anchor_hint_px <= 0 or image.height < 32:
        return image
    sample_width = min(256, image.width)
    grayscale = image.convert("L").resize(
        (sample_width, image.height), Image.Resampling.BOX
    )
    try:
        if hasattr(grayscale, "get_flattened_data"):
            pixels = list(grayscale.get_flattened_data())
        else:
            pixels = list(grayscale.getdata())
    finally:
        grayscale.close()

    dark_limit = max(2, round(sample_width * 0.006))
    row_has_ink: list[bool] = []
    for row in range(image.height):
        start = row * sample_width
        dark_count = sum(
            value < threshold for value in pixels[start : start + sample_width]
        )
        row_has_ink.append(dark_count > dark_limit)

    search_end = min(image.height, anchor_hint_px + max(2, round(dpi * 0.01)))
    min_blank_rows = max(8, round(dpi * 0.04))
    blank_runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for row in range(search_end + 1):
        blank = row == search_end or not row_has_ink[row]
        if blank and run_start is None:
            run_start = row
        elif not blank and run_start is not None:
            if row - run_start >= min_blank_rows:
                blank_runs.append((run_start, row))
            run_start = None
    if run_start is not None and search_end - run_start >= min_blank_rows:
        blank_runs.append((run_start, search_end))

    candidates = [
        (start, end)
        for start, end in blank_runs
        if start > 0
        and end <= search_end
        and any(row_has_ink[:start])
    ]
    if not candidates:
        return image
    _, separator_end = candidates[-1]
    keep_padding = max(10, round(dpi * 0.06))
    top = max(0, separator_end - keep_padding)
    if top < 3 or top >= image.height - 16:
        return image
    return image.crop((0, top, image.width, image.height))


def _trim_trailing_context(
    image: Image.Image,
    *,
    page_height_px: int,
    crop_top_y: float,
    dpi: int,
    threshold: int = 245,
) -> Image.Image:
    """Remove a small confirmation/footer block after the last question.

    This is deliberately conservative: the separator must be near the bottom
    of the source page and the ink after it must occupy only a short footer
    band.  Large diagrams and answer choices therefore remain part of the
    question.
    """

    if image.height < 64 or page_height_px <= 0:
        return image
    sample_width = min(256, image.width)
    grayscale = image.convert("L").resize(
        (sample_width, image.height), Image.Resampling.BOX
    )
    try:
        if hasattr(grayscale, "get_flattened_data"):
            pixels = list(grayscale.get_flattened_data())
        else:
            pixels = list(grayscale.getdata())
    finally:
        grayscale.close()

    dark_limit = max(2, round(sample_width * 0.006))
    row_has_ink: list[bool] = []
    for row in range(image.height):
        start = row * sample_width
        dark_count = sum(
            value < threshold for value in pixels[start : start + sample_width]
        )
        row_has_ink.append(dark_count > dark_limit)

    ink_rows = [row for row, has_ink in enumerate(row_has_ink) if has_ink]
    if not ink_rows:
        return image
    last_ink = ink_rows[-1]
    # A final question's choices are often followed by a short blank band
    # before the boxed "confirmation" footer.  The old 1.5% threshold missed
    # those 40–50 px bands at 300 DPI, so they leaked non-question text into
    # Q20.  Keep the floor at 0.8% of the page; the later footer size and
    # position checks still protect legitimate large question content.
    minimum_gap = max(round(page_height_px * 0.008), round(dpi * 0.08))
    maximum_footer_height = round(page_height_px * 0.12)
    candidates: list[tuple[int, int]] = []
    run_start: int | None = None
    for row in range(image.height + 1):
        blank = row < image.height and not row_has_ink[row]
        if blank and run_start is None:
            run_start = row
        elif not blank and run_start is not None:
            run_end = row
            if run_end - run_start >= minimum_gap:
                before = any(row_has_ink[:run_start])
                after_rows = [
                    index
                    for index in range(run_end, image.height)
                    if row_has_ink[index]
                ]
                if before and after_rows:
                    footer_first = after_rows[0]
                    separator_y = crop_top_y + run_start / page_height_px
                    footer_y = crop_top_y + footer_first / page_height_px
                    unusually_large_gap = (
                        run_end - run_start >= round(page_height_px * 0.08)
                    )
                    if (
                        (separator_y >= 0.70 or unusually_large_gap)
                        and footer_y >= 0.80
                        and last_ink - footer_first <= maximum_footer_height
                    ):
                        candidates.append((run_start, run_end))
            run_start = None

    if not candidates:
        return image
    # A confirmation box can contain several internal blank lines.  The first
    # qualifying separator is the boundary after the actual final question;
    # choosing a later one would retain the box title or its first line.
    separator_start, _ = candidates[0]
    keep_padding = max(12, round(dpi * 0.08))
    bottom = min(image.height, separator_start + keep_padding)
    if bottom >= image.height - 2:
        return image
    return image.crop((0, 0, image.width, bottom))


def _trim_narrow_page_footer(
    image: Image.Image,
    *,
    page_height_px: int,
    crop_top_y: float,
    dpi: int,
    threshold: int = 245,
) -> Image.Image:
    """Remove a detached narrow page number without touching answer choices.

    This runs for every fragment, unlike confirmation-footer trimming. It
    accepts only a small, detached, near-bottom ink block whose horizontal span
    is narrow. A second row of ①~⑤ choices spans much more of a column and
    must remain intact.
    """

    if image.height < 64 or page_height_px <= 0:
        return image
    sample_width = min(256, image.width)
    grayscale = image.convert("L").resize(
        (sample_width, image.height), Image.Resampling.BOX
    )
    try:
        if hasattr(grayscale, "get_flattened_data"):
            pixels = list(grayscale.get_flattened_data())
        else:
            pixels = list(grayscale.getdata())
    finally:
        grayscale.close()
    color_sample = image.convert("RGB").resize(
        (sample_width, image.height), Image.Resampling.BOX
    )
    try:
        if hasattr(color_sample, "get_flattened_data"):
            color_pixels = list(color_sample.get_flattened_data())
        else:
            color_pixels = list(color_sample.getdata())
    finally:
        color_sample.close()

    dark_limit = max(2, round(sample_width * 0.006))
    row_dark_columns: list[list[int]] = []
    for row in range(image.height):
        start = row * sample_width
        columns = [
            column
            for column, value in enumerate(pixels[start : start + sample_width])
            if value < threshold
        ]
        row_dark_columns.append(columns)
    row_has_ink = [len(columns) > dark_limit for columns in row_dark_columns]

    minimum_gap = max(round(page_height_px * 0.004), round(dpi * 0.035))
    maximum_footer_height = round(page_height_px * 0.05)
    maximum_colored_footer_height = round(page_height_px * 0.12)
    maximum_footer_span = max(20, round(sample_width * 0.18))
    run_start: int | None = None
    for row in range(image.height + 1):
        blank = row < image.height and not row_has_ink[row]
        if blank and run_start is None:
            run_start = row
            continue
        if blank or run_start is None:
            continue

        run_end = row
        if run_end - run_start >= minimum_gap and any(row_has_ink[:run_start]):
            footer_rows = [
                index for index in range(run_end, image.height) if row_has_ink[index]
            ]
            if footer_rows:
                footer_first = footer_rows[0]
                footer_last = footer_rows[-1]
                footer_columns = [
                    column
                    for index in footer_rows
                    for column in row_dark_columns[index]
                ]
                footer_y = crop_top_y + footer_first / page_height_px
                footer_span = (
                    max(footer_columns) - min(footer_columns) + 1
                    if footer_columns
                    else sample_width
                )
                first_block_last = footer_first
                while (
                    first_block_last + 1 < image.height
                    and row_has_ink[first_block_last + 1]
                ):
                    first_block_last += 1
                first_block_columns = [
                    column
                    for index in range(footer_first, first_block_last + 1)
                    for column in row_dark_columns[index]
                ]
                first_block_span = (
                    max(first_block_columns) - min(first_block_columns) + 1
                    if first_block_columns
                    else sample_width
                )
                footer_blue_pixels = sum(
                    blue >= red + 15
                    and blue >= green + 15
                    and red < 245
                    for index in footer_rows
                    for red, green, blue in color_pixels[
                        index * sample_width : (index + 1) * sample_width
                    ]
                )
                is_narrow_page_number = footer_span <= maximum_footer_span
                is_colored_copyright = footer_blue_pixels >= max(12, sample_width // 8)
                has_narrow_leading_page_number = (
                    first_block_span <= maximum_footer_span
                    and first_block_last - footer_first + 1 <= maximum_footer_height
                )
                footer_height = footer_last - footer_first + 1
                if (
                    run_start / page_height_px + crop_top_y >= 0.82
                    and (
                        (
                            footer_y >= 0.89
                            and is_narrow_page_number
                            and footer_height <= maximum_footer_height
                        )
                        or (
                            is_colored_copyright
                            and footer_height <= maximum_colored_footer_height
                            and (
                                footer_y >= 0.89
                                or (
                                    footer_y >= 0.85
                                    and has_narrow_leading_page_number
                                )
                            )
                        )
                    )
                ):
                    keep_padding = max(8, round(dpi * 0.035))
                    bottom = min(image.height, run_start + keep_padding)
                    if bottom < image.height - 2:
                        return image.crop((0, 0, image.width, bottom))
        run_start = None
    return image
