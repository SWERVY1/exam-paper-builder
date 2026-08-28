from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """Raised when a manifest cannot be used safely."""


ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    try:
        with manifest_path.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except FileNotFoundError as exc:
        raise ManifestError(f"Manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ManifestError("Manifest root must be a JSON object.")
    validate_manifest(data)
    return data


def resolve_source_pdf(manifest_path: str | Path, source_pdf: str) -> Path:
    candidate = Path(source_pdf)
    if not candidate.is_absolute():
        candidate = Path(manifest_path).resolve().parent / candidate
    return candidate.resolve()


def validate_manifest(manifest: dict[str, Any], page_count: int | None = None) -> None:
    errors: list[str] = []

    if manifest.get("version") != 1:
        errors.append("version must be 1")
    _validate_id(manifest.get("exam_id"), "exam_id", errors)
    if not isinstance(manifest.get("source_pdf"), str) or not manifest.get("source_pdf"):
        errors.append("source_pdf must be a non-empty string")

    render = manifest.get("render", {})
    if not isinstance(render, dict):
        errors.append("render must be an object")
        render = {}
    output_width = _positive_int(render, "output_width_px", 1800, errors)
    padding = _nonnegative_int(render, "padding_px", 60, errors)
    _positive_int(render, "dpi", 300, errors)
    _nonnegative_int(render, "gap_px", 36, errors)
    max_height = _nonnegative_int(render, "max_output_height_px", 24000, errors)
    if output_width is not None and padding is not None and output_width <= padding * 2:
        errors.append("output_width_px must be greater than twice padding_px")
    if max_height is not None and max_height and max_height < 256:
        errors.append("max_output_height_px must be 0 or at least 256")
    ink_ratio = render.get("min_ink_ratio", 0.002)
    if not isinstance(ink_ratio, (int, float)) or isinstance(ink_ratio, bool):
        errors.append("min_ink_ratio must be a number")
    elif not 0 <= float(ink_ratio) <= 1:
        errors.append("min_ink_ratio must be between 0 and 1")
    background = render.get("background", "#FFFFFF")
    if not isinstance(background, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", background):
        errors.append("background must use #RRGGBB format")
    output_format = render.get("output_format", "png")
    if output_format not in {"png", "webp"}:
        errors.append("output_format must be png or webp")
    store_fragments = render.get("store_fragments", True)
    if not isinstance(store_fragments, bool):
        errors.append("store_fragments must be true or false")

    fragments = _list_of_objects(manifest, "fragments", errors)
    stimuli = _list_of_objects(manifest, "stimuli", errors)
    questions = _list_of_objects(manifest, "questions", errors)

    fragment_ids = _unique_ids(fragments, "fragments", errors)
    stimulus_ids = _unique_ids(stimuli, "stimuli", errors)
    question_ids = _unique_ids(questions, "questions", errors)
    fragment_by_id = {
        item.get("id"): item for item in fragments if item.get("id") in fragment_ids
    }

    for index, fragment in enumerate(fragments):
        label = f"fragments[{index}]"
        kind = fragment.get("kind")
        if kind not in {"stimulus", "question"}:
            errors.append(f"{label}.kind must be stimulus or question")
        page = fragment.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            errors.append(f"{label}.page must be a positive integer")
        elif page_count is not None and page > page_count:
            errors.append(f"{label}.page {page} exceeds PDF page count {page_count}")
        bbox = fragment.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            errors.append(f"{label}.bbox must contain [x0, y0, x1, y1]")
        elif any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in bbox
        ):
            errors.append(f"{label}.bbox values must be numbers")
        else:
            x0, y0, x1, y1 = (float(value) for value in bbox)
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                errors.append(
                    f"{label}.bbox must satisfy 0 <= x0 < x1 <= 1 and "
                    "0 <= y0 < y1 <= 1"
                )

    for index, stimulus in enumerate(stimuli):
        label = f"stimuli[{index}]"
        refs = _id_list(
            stimulus.get("fragment_ids"),
            f"{label}.fragment_ids",
            errors,
            allow_empty=False,
        )
        for ref in refs:
            if ref not in fragment_ids:
                errors.append(f"{label} references unknown fragment {ref!r}")
            elif fragment_by_id[ref].get("kind") != "stimulus":
                errors.append(f"{label} references non-stimulus fragment {ref!r}")

    output_names: set[str] = set()
    for index, question in enumerate(questions):
        label = f"questions[{index}]"
        if question.get("id") not in question_ids:
            continue
        number = question.get("number")
        if not isinstance(number, (int, str)) or isinstance(number, bool):
            errors.append(f"{label}.number must be an integer or string")
        section = question.get("section")
        if not isinstance(section, str) or not section:
            errors.append(f"{label}.section must be a non-empty string")
        stimulus_refs = _id_list(
            question.get("stimulus_ids", []), f"{label}.stimulus_ids", errors
        )
        fragment_refs = _id_list(
            question.get("fragment_ids"),
            f"{label}.fragment_ids",
            errors,
            allow_empty=False,
        )
        for ref in stimulus_refs:
            if ref not in stimulus_ids:
                errors.append(f"{label} references unknown stimulus {ref!r}")
        for ref in fragment_refs:
            if ref not in fragment_ids:
                errors.append(f"{label} references unknown fragment {ref!r}")
            elif fragment_by_id[ref].get("kind") != "question":
                errors.append(f"{label} references non-question fragment {ref!r}")

        export = question.get("export", {})
        if export is None:
            export = {}
        if not isinstance(export, dict):
            errors.append(f"{label}.export must be an object")
        else:
            extension = ".webp" if output_format == "webp" else ".png"
            filename = export.get("filename", f"{question.get('id', '')}{extension}")
            if not isinstance(filename, str) or not filename.lower().endswith(extension):
                errors.append(f"{label}.export.filename must end with {extension}")
            elif Path(filename).name != filename or filename in {".", ".."}:
                errors.append(f"{label}.export.filename must be a plain filename")
            elif filename.casefold() in output_names:
                errors.append(f"duplicate export filename: {filename}")
            else:
                output_names.add(filename.casefold())

    if errors:
        rendered = "\n".join(f"- {message}" for message in errors)
        raise ManifestError(f"Manifest validation failed:\n{rendered}")


def _validate_id(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        errors.append(f"{label} must match {ID_PATTERN.pattern}")


def _list_of_objects(
    manifest: dict[str, Any], key: str, errors: list[str]
) -> list[dict[str, Any]]:
    value = manifest.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be an array")
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{key}[{index}] must be an object")
        else:
            result.append(item)
    return result


def _unique_ids(
    items: list[dict[str, Any]], label: str, errors: list[str]
) -> set[str]:
    seen: set[str] = set()
    for index, item in enumerate(items):
        value = item.get("id")
        _validate_id(value, f"{label}[{index}].id", errors)
        if not isinstance(value, str):
            continue
        if value in seen:
            errors.append(f"duplicate {label} id: {value}")
        seen.add(value)
    return seen


def _id_list(
    value: Any, label: str, errors: list[str], allow_empty: bool = True
) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    if not allow_empty and not value:
        errors.append(f"{label} must not be empty")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not ID_PATTERN.fullmatch(item):
            errors.append(f"{label}[{index}] is not a valid id")
            continue
        if item in seen:
            errors.append(f"{label} contains duplicate id {item!r}")
        seen.add(item)
        result.append(item)
    return result


def _positive_int(
    container: dict[str, Any], key: str, default: int, errors: list[str]
) -> int | None:
    value = container.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{key} must be a positive integer")
        return None
    return value


def _nonnegative_int(
    container: dict[str, Any], key: str, default: int, errors: list[str]
) -> int | None:
    value = container.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{key} must be a non-negative integer")
        return None
    return value
