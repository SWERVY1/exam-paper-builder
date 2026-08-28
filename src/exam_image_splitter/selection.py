"""Deterministic selection-manifest generation for composition inputs."""

from __future__ import annotations

import copy
import json
import os
import random
import uuid
from pathlib import Path
from typing import Any, Iterable


class SelectionError(ValueError):
    """Raised when an exact, reproducible selection cannot be produced."""


def select_manifest(
    source_manifest: str | Path,
    output_manifest: str | Path,
    *,
    count: int,
    seed: int,
    bundle_policy: str = "question",
    primary_categories: Iterable[str] = (),
    subtypes: Iterable[str] = (),
    unit_prefixes: Iterable[str] = (),
    excluded_ids: Iterable[str] = (),
    numbering_start: int = 1,
) -> Path:
    """Write an exact-count selection using stable ordering and a recorded seed."""

    source = Path(source_manifest).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if count <= 0:
        raise SelectionError("count must be a positive integer")
    if numbering_start <= 0:
        raise SelectionError("numbering_start must be a positive integer")
    if bundle_policy not in {"question", "whole-bundle"}:
        raise SelectionError("bundle_policy must be question or whole-bundle")

    manifest = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise SelectionError("source manifest must be a version 1 composition manifest")
    questions = manifest.get("questions")
    if not isinstance(questions, list) or not questions:
        raise SelectionError("source manifest questions must be a non-empty array")
    if any(not isinstance(item, dict) or not str(item.get("id", "")).strip() for item in questions):
        raise SelectionError("every question requires a non-empty id")
    question_ids = [str(item["id"]) for item in questions]
    if len(question_ids) != len(set(question_ids)):
        raise SelectionError("question ids must be unique")

    primary = tuple(sorted({str(value) for value in primary_categories if str(value)}))
    subtype = tuple(sorted({str(value) for value in subtypes if str(value)}))
    unit = tuple(sorted({str(value) for value in unit_prefixes if str(value)}))
    excluded = {str(value) for value in excluded_ids if str(value)}
    unknown_excluded = sorted(excluded.difference(question_ids))
    if unknown_excluded:
        raise SelectionError(
            "excluded question ids are unknown: " + ", ".join(unknown_excluded)
        )

    indexed = list(enumerate(questions))
    matches = {
        str(question["id"]): (
            str(question["id"]) not in excluded
            and _matches_filters(
                question,
                primary_categories=primary,
                subtypes=subtype,
                unit_prefixes=unit,
            )
        )
        for _index, question in indexed
    }
    rng = random.Random(int(seed))

    candidate_bundle_count: int | None = None
    excluded_bundle_count: int | None = None
    if bundle_policy == "question":
        candidates = sorted(
            [question for _index, question in indexed if matches[str(question["id"])]],
            key=lambda question: str(question["id"]),
        )
        if len(candidates) < count:
            raise SelectionError(
                f"exact selection impossible: requested {count}, only "
                f"{len(candidates)} eligible questions"
            )
        selected = rng.sample(candidates, count)
        candidate_count = len(candidates)
    else:
        groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for original_index, question in indexed:
            bundle_id = str(question.get("bundle_id") or question["id"])
            groups.setdefault(bundle_id, []).append((original_index, question))
        eligible_groups = [
            (bundle_id, members)
            for bundle_id, members in sorted(groups.items(), key=lambda item: item[0])
            if all(matches[str(question["id"])] for _index, question in members)
        ]
        candidate_bundle_count = len(eligible_groups)
        excluded_bundle_count = len(groups) - len(eligible_groups)
        rng.shuffle(eligible_groups)
        chosen_group_indexes = _exact_group_subset(eligible_groups, count)
        if chosen_group_indexes is None:
            sizes = sorted(len(members) for _bundle, members in eligible_groups)
            raise SelectionError(
                "exact whole-bundle selection impossible: "
                f"requested {count}, eligible bundle sizes are {sizes}"
            )
        selected = []
        for group_index in chosen_group_indexes:
            _bundle_id, members = eligible_groups[group_index]
            selected.extend(
                question for _original_index, question in sorted(members)
            )
        candidate_count = sum(len(members) for _bundle, members in eligible_groups)

    if len(selected) != count:
        raise SelectionError(
            f"internal selection error: selected {len(selected)}, expected {count}"
        )
    selected_ids = [str(question["id"]) for question in selected]
    result = copy.deepcopy(manifest)
    result["numbering_start"] = numbering_start
    result["selection"] = [
        {"question_id": question_id, "new_number": numbering_start + index}
        for index, question_id in enumerate(selected_ids)
    ]
    audit: dict[str, Any] = {
        "schema_version": 1,
        "source_manifest": str(source),
        "seed": int(seed),
        "bundle_policy": bundle_policy,
        "requested_count": count,
        "candidate_count": candidate_count,
        "excluded_count": len(questions) - candidate_count,
        "filters": {
            "primary_categories": list(primary),
            "subtypes": list(subtype),
            "unit_prefixes": list(unit),
            "excluded_ids": sorted(excluded),
        },
        "selected_question_ids": selected_ids,
        "selected_bundle_ids": [
            str(question.get("bundle_id") or question["id"])
            for question in selected
        ],
    }
    if candidate_bundle_count is not None:
        audit["candidate_bundle_count"] = candidate_bundle_count
        audit["excluded_bundle_count"] = excluded_bundle_count
    result["selection_audit"] = audit

    destination = Path(output_manifest).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _matches_filters(
    question: dict[str, Any],
    *,
    primary_categories: tuple[str, ...],
    subtypes: tuple[str, ...],
    unit_prefixes: tuple[str, ...],
) -> bool:
    classification = question.get("classification")
    classification = classification if isinstance(classification, dict) else {}
    if primary_categories and str(classification.get("primary_category", "")) not in primary_categories:
        return False
    if subtypes and str(classification.get("subtype", "")) not in subtypes:
        return False
    unit_value = classification.get("unit_path", "")
    if isinstance(unit_value, list):
        unit_path = "/".join(str(value) for value in unit_value)
    else:
        unit_path = str(unit_value)
    if unit_prefixes and not any(unit_path.startswith(prefix) for prefix in unit_prefixes):
        return False
    return True


def _exact_group_subset(
    groups: list[tuple[str, list[tuple[int, dict[str, Any]]]]],
    requested_count: int,
) -> list[int] | None:
    choices: dict[int, list[int]] = {0: []}
    for group_index, (_bundle_id, members) in enumerate(groups):
        size = len(members)
        for subtotal, selected_indexes in sorted(
            list(choices.items()), reverse=True
        ):
            new_total = subtotal + size
            if new_total <= requested_count and new_total not in choices:
                choices[new_total] = [*selected_indexes, group_index]
    return choices.get(requested_count)
