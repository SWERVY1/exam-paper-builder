"""Deterministic pairing for local problem and solution PDF corpora."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any


def normalized_exam_stem(path: str | Path) -> str:
    """Return the shared exam stem used by a problem/solution pair.

    Solution files in the local corpus conventionally append `` 해설`` to the
    problem stem.  Only that final suffix is removed; all other characters are
    significant so that similar exams are never paired heuristically.
    """

    stem = Path(path).stem.strip()
    if stem.endswith(" 해설"):
        stem = stem[: -len(" 해설")].rstrip()
    return stem


def pair_exam_directories(
    problem_dir: str | Path,
    solution_dir: str | Path,
) -> dict[str, Any]:
    """Pair PDFs by exact normalized stem and report every ambiguity."""

    problem_root = Path(problem_dir).resolve()
    solution_root = Path(solution_dir).resolve()
    if not problem_root.is_dir():
        raise FileNotFoundError(problem_root)
    if not solution_root.is_dir():
        raise FileNotFoundError(solution_root)

    problems = _index_pdfs(problem_root, solution_names=False)
    solutions = _index_pdfs(solution_root, solution_names=True)
    all_stems = sorted(set(problems) | set(solutions), key=str.casefold)

    pairs: list[dict[str, str]] = []
    problem_only: list[dict[str, Any]] = []
    solution_only: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for stem in all_stems:
        problem_paths = problems.get(stem, [])
        solution_paths = solutions.get(stem, [])
        if len(problem_paths) == 1 and len(solution_paths) == 1:
            pairs.append(
                {
                    "pair_id": stem,
                    "problem_pdf": str(problem_paths[0]),
                    "solution_pdf": str(solution_paths[0]),
                    "pairing_status": "matched_by_exact_stem",
                }
            )
        elif len(problem_paths) > 1 or len(solution_paths) > 1:
            ambiguous.append(
                {
                    "pair_id": stem,
                    "problem_pdfs": [str(path) for path in problem_paths],
                    "solution_pdfs": [str(path) for path in solution_paths],
                    "pairing_status": "ambiguous_duplicate_stem",
                }
            )
        elif problem_paths:
            problem_only.append(
                {
                    "pair_id": stem,
                    "problem_pdf": str(problem_paths[0]),
                    "pairing_status": "problem_only",
                }
            )
        else:
            solution_only.append(
                {
                    "pair_id": stem,
                    "solution_pdf": str(solution_paths[0]),
                    "pairing_status": "solution_only",
                }
            )

    return {
        "schema_version": 1,
        "problem_dir": str(problem_root),
        "solution_dir": str(solution_root),
        "problem_pdf_count": sum(len(paths) for paths in problems.values()),
        "solution_pdf_count": sum(len(paths) for paths in solutions.values()),
        "pair_count": len(pairs),
        "problem_only_count": len(problem_only),
        "solution_only_count": len(solution_only),
        "ambiguous_count": len(ambiguous),
        "pairs": pairs,
        "problem_only": problem_only,
        "solution_only": solution_only,
        "ambiguous": ambiguous,
    }


def _index_pdfs(root: Path, *, solution_names: bool) -> dict[str, list[Path]]:
    result: defaultdict[str, list[Path]] = defaultdict(list)
    for path in sorted(root.rglob("*.pdf"), key=lambda item: str(item).casefold()):
        stem = normalized_exam_stem(path) if solution_names else path.stem.strip()
        result[stem].append(path.resolve())
    return dict(result)
