from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .anchors import detect_structure_anchors
from .answers import extract_answer_key, propose_solution_manifest
from .audit import audit_anchor_directory, write_anchor_audit
from .batch import read_batch_status, run_batch_generation
from .build import build_exam, sha256_file
from .composition import CompositionError, compose_exam
from .inventory import inventory_directory, write_inventory
from .manifest import ManifestError, load_manifest, resolve_source_pdf, validate_manifest
from .pdf_render import PdfRenderer
from .pairing import pair_exam_directories
from .propose import propose_manifest
from .selection import select_manifest


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exam-split",
        description="PDF 본문 OCR 없이 문항별 이미지를 조립합니다.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="PDF 페이지 정보를 확인합니다."
    )
    inspect_parser.add_argument("pdf")
    inspect_parser.add_argument("--output", help="JSON을 저장할 경로")

    render_parser = subparsers.add_parser(
        "render-pages", help="좌표 지정용 페이지 PNG를 만듭니다."
    )
    render_parser.add_argument("pdf")
    render_parser.add_argument("output_dir")
    render_parser.add_argument("--dpi", type=int, default=160)
    render_parser.add_argument("--pages", help="예: 1-3,5,8-10")

    validate_parser = subparsers.add_parser(
        "validate", help="Manifest를 검증합니다."
    )
    validate_parser.add_argument("manifest")

    anchors_parser = subparsers.add_parser(
        "detect-anchors",
        help="PDF 텍스트 레이어에서 문제번호와 공통 범위 좌표만 찾습니다.",
    )
    anchors_parser.add_argument("pdf")
    anchors_parser.add_argument("--output", required=True)
    anchors_parser.add_argument(
        "--expected-count",
        type=int,
        help="이미지형 PDF OCR fallback에서 반드시 확인할 문항 수",
    )

    propose_parser = subparsers.add_parser(
        "propose",
        help="자동 탐지 좌표로 검수 전 Manifest 초안을 만듭니다.",
    )
    propose_parser.add_argument("pdf")
    propose_parser.add_argument("output_manifest")
    propose_parser.add_argument("--exam-id", required=True)
    propose_parser.add_argument("--dpi", type=int, default=300)
    propose_parser.add_argument("--output-width", type=int, default=1080)
    propose_parser.add_argument(
        "--format", choices=("png", "webp"), default="webp"
    )
    propose_parser.add_argument("--store-fragments", action="store_true")
    propose_parser.add_argument(
        "--expected-count",
        type=int,
        help="이미지형 PDF OCR fallback에서 반드시 확인할 문항 수",
    )

    solution_propose_parser = subparsers.add_parser(
        "propose-solution",
        help="답지 전용 레이아웃으로 문항별 해설 Manifest 초안을 만듭니다.",
    )
    solution_propose_parser.add_argument("pdf")
    solution_propose_parser.add_argument("output_manifest")
    solution_propose_parser.add_argument("--exam-id", required=True)
    solution_propose_parser.add_argument("--dpi", type=int, default=300)
    solution_propose_parser.add_argument("--output-width", type=int, default=1080)
    solution_propose_parser.add_argument(
        "--format", choices=("png", "webp"), default="webp"
    )
    solution_propose_parser.add_argument("--expected-count", type=int, default=20)
    solution_propose_parser.add_argument("--store-fragments", action="store_true")

    pair_parser = subparsers.add_parser(
        "pair-corpus",
        help="문제/해설 PDF를 정확한 파일 stem으로 짝지어 보고합니다.",
    )
    pair_parser.add_argument("problem_dir")
    pair_parser.add_argument("solution_dir")
    pair_parser.add_argument("output_json")

    answer_key_parser = subparsers.add_parser(
        "extract-answer-key",
        help="해설 PDF 텍스트 레이어에서 객관식 정답표를 추출합니다.",
    )
    answer_key_parser.add_argument("pdf")
    answer_key_parser.add_argument("output_json")
    answer_key_parser.add_argument("--expected-count", type=int, required=True)

    compose_parser = subparsers.add_parser(
        "compose-exam",
        help="선택 manifest로 새 번호의 문제지와 답지 PDF를 만듭니다.",
    )
    compose_parser.add_argument("manifest")
    compose_parser.add_argument("--output", default="output/pdf", help="출력 루트")
    compose_parser.add_argument(
        "--no-render-qa",
        action="store_true",
        help="전 페이지 QA PNG 렌더링을 생략합니다.",
    )
    compose_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="같은 build_id의 완성 출력이 있을 때만 명시적으로 교체합니다.",
    )

    select_parser = subparsers.add_parser(
        "select-manifest",
        help="필터와 seed로 정확한 수의 선택 manifest를 재현 가능하게 만듭니다.",
    )
    select_parser.add_argument("source_manifest")
    select_parser.add_argument("output_manifest")
    select_parser.add_argument("--count", type=int, required=True)
    select_parser.add_argument("--seed", type=int, required=True)
    select_parser.add_argument(
        "--bundle-policy",
        choices=("question", "whole-bundle"),
        default="question",
    )
    select_parser.add_argument("--primary-category", action="append", default=[])
    select_parser.add_argument("--subtype", action="append", default=[])
    select_parser.add_argument("--unit-prefix", action="append", default=[])
    select_parser.add_argument("--exclude-id", action="append", default=[])
    select_parser.add_argument("--numbering-start", type=int, default=1)

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="PDF 폴더의 페이지, 크기, 중복 해시를 조사합니다.",
    )
    inventory_parser.add_argument("source_dir")
    inventory_parser.add_argument("output_json")
    inventory_parser.add_argument("--limit", type=int)
    inventory_parser.add_argument("--no-hash", action="store_true")

    audit_parser = subparsers.add_parser(
        "audit-anchors",
        help="PDF 폴더 전체의 자동 문제번호 탐지 가능 범위를 측정합니다.",
    )
    audit_parser.add_argument("source_dir")
    audit_parser.add_argument("output_json")
    audit_parser.add_argument("--limit", type=int)

    build_parser = subparsers.add_parser(
        "build", help="문항별 이미지를 빌드합니다."
    )
    build_parser.add_argument("manifest")
    build_parser.add_argument("--output", default="output", help="출력 루트")
    build_parser.add_argument("--only", help="쉼표로 구분한 문항 ID")

    preview_parser = subparsers.add_parser(
        "preview-proposal",
        help="needs_review Manifest의 검수용 이미지를 만듭니다.",
    )
    preview_parser.add_argument("manifest")
    preview_parser.add_argument("--output", default="work/proposal-preview")
    preview_parser.add_argument("--only", help="쉼표로 구분한 문항 ID")
    batch_parser = subparsers.add_parser(
        "batch-generate",
        help="감사 결과를 기준으로 문항별 무손실 WebP를 재개 가능하게 생성합니다.",
    )
    batch_parser.add_argument("source_dir")
    batch_parser.add_argument("inventory_json")
    batch_parser.add_argument("audit_json")
    batch_parser.add_argument("output_root")
    batch_parser.add_argument("--dpi", type=int, default=300)
    batch_parser.add_argument("--output-width", type=int, default=1080)
    batch_parser.add_argument("--min-free-gib", type=float, default=8.0)
    batch_parser.add_argument("--limit", type=int)
    batch_parser.add_argument("--retry-failed", action="store_true")

    status_parser = subparsers.add_parser(
        "batch-status", help="전체 이미지 생성 대기열의 현재 상태를 표시합니다."
    )
    status_parser.add_argument("output_root")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            return _inspect(args.pdf, args.output)
        if args.command == "render-pages":
            return _render_pages(args.pdf, args.output_dir, args.dpi, args.pages)
        if args.command == "validate":
            return _validate(args.manifest)
        if args.command == "detect-anchors":
            result = detect_structure_anchors(
                args.pdf, expected_question_count=args.expected_count
            )
            destination = _write_json(args.output, result)
            summary = result["summary"]
            print(
                f"완료: 문제번호 {summary['question_anchor_count']}개, "
                f"공통 범위 {summary['group_anchor_count']}개 -> {destination}"
            )
            return 0
        if args.command == "propose":
            manifest = propose_manifest(
                args.pdf,
                args.output_manifest,
                args.exam_id,
                dpi=args.dpi,
                output_width_px=args.output_width,
                output_format=args.format,
                store_fragments=args.store_fragments,
                expected_question_count=args.expected_count,
            )
            destination = _write_json(args.output_manifest, manifest)
            print(
                f"검수 필요: {len(manifest['questions'])}개 문항 초안 -> {destination}"
            )
            return 0
        if args.command == "propose-solution":
            manifest = propose_solution_manifest(
                args.pdf,
                args.output_manifest,
                args.exam_id,
                dpi=args.dpi,
                output_width_px=args.output_width,
                output_format=args.format,
                expected_count=args.expected_count,
                store_fragments=args.store_fragments,
            )
            destination = _write_json(args.output_manifest, manifest)
            print(
                f"답지 초안 완료: {len(manifest['questions'])}개 문항 -> {destination}"
            )
            return 0
        if args.command == "pair-corpus":
            pairing = pair_exam_directories(args.problem_dir, args.solution_dir)
            destination = _write_json(args.output_json, pairing)
            print(
                f"완료: 짝 {pairing['pair_count']}개, 문제만 "
                f"{pairing['problem_only_count']}개, 해설만 "
                f"{pairing['solution_only_count']}개, 모호 "
                f"{pairing['ambiguous_count']}개 -> {destination}"
            )
            return 0
        if args.command == "extract-answer-key":
            answer_key = extract_answer_key(
                args.pdf, expected_count=args.expected_count
            )
            result = {
                "schema_version": 1,
                "source_pdf": str(Path(args.pdf).resolve()),
                "expected_count": args.expected_count,
                "answers": [
                    {"original_number": number, "answer": answer_key[number]}
                    for number in range(1, args.expected_count + 1)
                ],
            }
            destination = _write_json(args.output_json, result)
            print(f"완료: 정답 {len(answer_key)}개 -> {destination}")
            return 0
        if args.command == "compose-exam":
            report = compose_exam(
                args.manifest,
                args.output,
                render_qa=not args.no_render_qa,
                overwrite=args.overwrite,
            )
            print(f"완료: 문제지 1개 + 답지 1개 -> {report}")
            return 0
        if args.command == "select-manifest":
            destination = select_manifest(
                args.source_manifest,
                args.output_manifest,
                count=args.count,
                seed=args.seed,
                bundle_policy=args.bundle_policy,
                primary_categories=args.primary_category,
                subtypes=args.subtype,
                unit_prefixes=args.unit_prefix,
                excluded_ids=args.exclude_id,
                numbering_start=args.numbering_start,
            )
            print(f"완료: 재현 가능한 선택 manifest -> {destination}")
            return 0
        if args.command == "inventory":
            inventory = inventory_directory(
                args.source_dir,
                include_hashes=not args.no_hash,
                limit=args.limit,
            )
            destination = write_inventory(args.output_json, inventory)
            print(
                f"완료: {inventory['file_count']}개 PDF, "
                f"{inventory['total_pages']}쪽, 오류 {inventory['error_count']}개 -> "
                f"{destination}"
            )
            return 0
        if args.command == "audit-anchors":
            audit = audit_anchor_directory(args.source_dir, limit=args.limit)
            destination = write_anchor_audit(args.output_json, audit)
            status_text = ", ".join(
                f"{key}={value}" for key, value in sorted(audit["status_counts"].items())
            )
            print(f"완료: {audit['file_count']}개 PDF ({status_text}) -> {destination}")
            return 0
        if args.command == "build":
            only = (
                [item.strip() for item in args.only.split(",") if item.strip()]
                if args.only
                else None
            )
            report = build_exam(args.manifest, args.output, only)
            print(f"완료: {report}")
            return 0
        if args.command == "preview-proposal":
            only = (
                [item.strip() for item in args.only.split(",") if item.strip()]
                if args.only
                else None
            )
            report = build_exam(
                args.manifest,
                args.output,
                only,
                preview_only=True,
            )
            print(f"검수 전용 미리보기: {report}")
            return 0
        if args.command == "batch-generate":
            status = run_batch_generation(
                args.source_dir,
                args.inventory_json,
                args.audit_json,
                args.output_root,
                dpi=args.dpi,
                output_width_px=args.output_width,
                min_free_gib=args.min_free_gib,
                limit=args.limit,
                retry_failed=args.retry_failed,
            )
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0 if not status.get("stopped_reason") else 3
        if args.command == "batch-status":
            status = read_batch_status(args.output_root)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
    except (
        CompositionError,
        ManifestError,
        FileNotFoundError,
        ValueError,
        IndexError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    return 1


def _inspect(pdf_path: str, output: str | None) -> int:
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with PdfRenderer(path, dpi=72) as renderer:
        pages = []
        page_count = renderer.page_count
        for page_number in range(1, page_count + 1):
            width, height = renderer.page_size_points(page_number)
            pages.append(
                {
                    "page": page_number,
                    "width_points": round(width, 3),
                    "height_points": round(height, 3),
                }
            )
    result = {
        "source_pdf": str(path),
        "sha256": sha256_file(path),
        "page_count": page_count,
        "pages": pages,
        "has_body_text_ocr_output": False,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        destination = Path(output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
        print(f"저장: {destination}")
    else:
        print(rendered)
    return 0


def _render_pages(
    pdf_path: str,
    output_dir: str,
    dpi: int,
    pages_spec: str | None,
) -> int:
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with PdfRenderer(path, dpi=dpi, cache_pages=1) as renderer:
        pages = _parse_pages(pages_spec, renderer.page_count)
        for page_number in pages:
            image = renderer.render_page(page_number)
            image.save(destination / f"page_{page_number:04d}.png", format="PNG")
    print(f"완료: {len(pages)}개 페이지 -> {destination}")
    return 0


def _validate(manifest_path: str) -> int:
    manifest = load_manifest(manifest_path)
    source = resolve_source_pdf(manifest_path, manifest["source_pdf"])
    if not source.is_file():
        raise ManifestError(f"Source PDF not found: {source}")
    with PdfRenderer(source, dpi=72) as renderer:
        validate_manifest(manifest, renderer.page_count)
    print(f"유효함: {manifest['exam_id']} ({len(manifest['questions'])}개 문항)")
    return 0


def _parse_pages(spec: str | None, page_count: int) -> list[int]:
    if not spec:
        return list(range(1, page_count + 1))
    selected: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"Invalid page range: {token}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    if not selected or min(selected) < 1 or max(selected) > page_count:
        raise ValueError(f"Pages must be inside 1..{page_count}")
    return sorted(selected)


def _write_json(path: str | Path, value: object) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
