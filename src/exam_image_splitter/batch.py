from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from PIL import Image

from .build import build_exam
from .propose import propose_manifest


TERMINAL_GENERATION_STATUSES = {
    "generated_staging",
    "generated_with_warnings",
    "needs_anchor_review",
    "needs_structure_ocr",
}


def stable_exam_id(relative_path: str, source_sha256: str) -> str:
    path_digest = hashlib.blake2s(
        relative_path.replace("\\", "/").encode("utf-8"), digest_size=4
    ).hexdigest()
    return f"doc_{source_sha256[:12]}_{path_digest}"


def run_batch_generation(
    source_dir: str | Path,
    inventory_path: str | Path,
    audit_path: str | Path,
    output_root: str | Path,
    *,
    dpi: int = 300,
    output_width_px: int = 1080,
    min_free_gib: float = 8.0,
    limit: int | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    source_root = Path(source_dir).resolve()
    inventory_file = Path(inventory_path).resolve()
    audit_file = Path(audit_path).resolve()
    destination = Path(output_root).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    if not inventory_file.is_file():
        raise FileNotFoundError(inventory_file)
    if not audit_file.is_file():
        raise FileNotFoundError(audit_file)
    if dpi <= 0 or output_width_px <= 0 or min_free_gib < 0:
        raise ValueError("dpi/output width must be positive and free-space guard nonnegative")

    destination.mkdir(parents=True, exist_ok=True)
    batch_dir = destination / "_batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    database_path = batch_dir / "batch-state.sqlite3"
    lock_path = batch_dir / "generation.lock"

    inventory = json.loads(inventory_file.read_text(encoding="utf-8"))
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    _validate_source_reports(source_root, inventory, audit)

    with _exclusive_lock(lock_path):
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            _initialize_database(connection)
            _sync_queue(connection, inventory, audit)
            _write_status_snapshot(connection, destination, stopped_reason=None)
            processed = 0
            attempted_paths: set[str] = set()
            stopped_reason: str | None = None
            eligible = ["queued"]
            if retry_failed:
                eligible.extend(["failed", "failed_validation"])

            while limit is None or processed < limit:
                row = _next_unattempted_document(
                    connection, eligible, attempted_paths
                )
                if row is None:
                    break
                attempted_paths.add(str(row["relative_path"]))

                free_bytes = shutil.disk_usage(destination).free
                if free_bytes < int(min_free_gib * 1024**3):
                    stopped_reason = (
                        f"low_disk_space: {free_bytes / 1024**3:.2f} GiB free "
                        f"(< {min_free_gib:.2f} GiB)"
                    )
                    break

                processed += 1
                _mark_running(connection, row["relative_path"])
                print(
                    f"[{processed}] {row['relative_path']} -> {row['exam_id']}",
                    flush=True,
                )
                try:
                    result = _generate_one(
                        source_root=source_root,
                        destination=destination,
                        row=row,
                        dpi=dpi,
                        output_width_px=output_width_px,
                    )
                except ValidationFailure as exc:
                    quarantine_path = _quarantine_failed_output(
                        destination, row["exam_id"]
                    )
                    error = str(exc)
                    if quarantine_path is not None:
                        error += f"; quarantined at {quarantine_path}"
                    _mark_failed(
                        connection,
                        row["relative_path"],
                        "failed_validation",
                        error,
                    )
                    print(f"  validation failed: {exc}", flush=True)
                except Exception as exc:  # keep the full batch moving
                    quarantine_path = _quarantine_failed_output(
                        destination, row["exam_id"]
                    )
                    error = f"{type(exc).__name__}: {exc}"
                    if quarantine_path is not None:
                        error += f"; quarantined at {quarantine_path}"
                    _mark_failed(
                        connection,
                        row["relative_path"],
                        "failed",
                        error,
                    )
                    print(f"  failed: {type(exc).__name__}: {exc}", flush=True)
                else:
                    _mark_generated(connection, row["relative_path"], result)
                    print(
                        f"  {result['generation_status']}: "
                        f"{result['question_images']} question images, "
                        f"{result['output_bytes'] / 1024**2:.2f} MiB",
                        flush=True,
                    )
                _write_status_snapshot(connection, destination, stopped_reason=None)

            return _write_status_snapshot(
                connection,
                destination,
                stopped_reason=stopped_reason,
            )
        finally:
            connection.close()


def read_batch_status(output_root: str | Path) -> dict[str, Any]:
    destination = Path(output_root).resolve()
    database_path = destination / "_batch" / "batch-state.sqlite3"
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return _status_snapshot(connection, destination, stopped_reason=None)
    finally:
        connection.close()


def _next_unattempted_document(
    connection: sqlite3.Connection,
    eligible_statuses: list[str],
    attempted_paths: set[str],
) -> sqlite3.Row | None:
    """Return each eligible document at most once per batch invocation."""

    placeholders = ",".join("?" for _ in eligible_statuses)
    rows = connection.execute(
        f"""
        SELECT * FROM documents
        WHERE generation_status IN ({placeholders})
        ORDER BY relative_path
        """,
        eligible_statuses,
    ).fetchall()
    return next(
        (
            row
            for row in rows
            if str(row["relative_path"]) not in attempted_paths
        ),
        None,
    )


def _generate_one(
    *,
    source_root: Path,
    destination: Path,
    row: sqlite3.Row,
    dpi: int,
    output_width_px: int,
) -> dict[str, Any]:
    source_pdf = source_root / row["relative_path"]
    if not source_pdf.is_file():
        raise FileNotFoundError(source_pdf)
    manifests_dir = destination / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifests_dir / f"{row['exam_id']}.json"
    manifest = propose_manifest(
        source_pdf,
        manifest_path,
        row["exam_id"],
        dpi=dpi,
        output_width_px=output_width_px,
        output_format="webp",
        store_fragments=False,
        expected_question_count=int(row["expected_question_count"]),
    )
    proposal_warnings = list(manifest.get("proposal", {}).get("warnings", []))
    if proposal_warnings:
        raise ValidationFailure(
            "automatic proposal needs review: " + "; ".join(proposal_warnings[:3])
        )
    expected_count = int(row["expected_question_count"])
    if len(manifest["questions"]) != expected_count:
        raise ValidationFailure(
            f"proposal count {len(manifest['questions'])} != audited {expected_count}"
        )
    _write_json_atomic(manifest_path, manifest)

    report_path = build_exam(
        manifest_path,
        destination / "staging",
        preview_only=True,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("source_sha256") != row["source_sha256"]:
        raise ValidationFailure("source hash changed after inventory")
    if len(report.get("questions", [])) != expected_count:
        raise ValidationFailure(
            f"build count {len(report.get('questions', []))} != audited {expected_count}"
        )

    output_paths: list[Path] = []
    seen_question_ids: set[str] = set()
    for question in report["questions"]:
        question_id = question["id"]
        if question_id in seen_question_ids:
            raise ValidationFailure(f"duplicate question id {question_id}")
        seen_question_ids.add(question_id)
        outputs = question.get("outputs", [])
        if len(outputs) != 1:
            raise ValidationFailure(
                f"{question_id} produced {len(outputs)} files; exactly one is required"
            )
        output_path = report_path.parent / outputs[0]["path"]
        if output_path.suffix.lower() != ".webp" or not output_path.is_file():
            raise ValidationFailure(f"missing lossless WebP for {question_id}")
        _verify_image(output_path, expected_width=output_width_px)
        output_paths.append(output_path)

    if len(output_paths) != expected_count or len(set(output_paths)) != expected_count:
        raise ValidationFailure("question images are missing or filenames repeat")
    warnings = list(report.get("warnings", []))
    edge_warnings = [
        warning
        for warning in warnings
        if warning.startswith(("OUTER_EDGE_INK:", "BOTTOM_EDGE_INK:"))
    ]
    if edge_warnings:
        raise ValidationFailure(
            "possible horizontal clipping detected: " + "; ".join(edge_warnings[:3])
        )
    return {
        "generation_status": (
            "generated_with_warnings" if warnings else "generated_staging"
        ),
        "question_images": len(output_paths),
        "output_bytes": sum(path.stat().st_size for path in output_paths),
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "warning_count": len(warnings),
    }


def _quarantine_failed_output(destination: Path, exam_id: str) -> Path | None:
    """Move a failed document out of staging without deleting diagnostic files."""

    source = destination / "staging" / exam_id
    if not source.is_dir():
        return None
    quarantine_root = destination / "quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    target = quarantine_root / exam_id
    attempt = 2
    while target.exists():
        target = quarantine_root / f"{exam_id}-attempt-{attempt:02d}"
        attempt += 1
    source.replace(target)
    return target


def _verify_image(path: Path, expected_width: int) -> None:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        if image.format != "WEBP":
            raise ValidationFailure(f"unexpected image format for {path.name}")
        if image.width != expected_width or image.height <= 0:
            raise ValidationFailure(
                f"invalid dimensions for {path.name}: {image.width}x{image.height}"
            )


def _validate_source_reports(
    source_root: Path,
    inventory: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    inventory_source = Path(inventory.get("source_dir", "")).resolve()
    audit_source = Path(audit.get("source_dir", "")).resolve()
    if inventory_source != source_root or audit_source != source_root:
        raise ValueError("inventory/audit source_dir does not match the requested source")
    if inventory.get("file_count") != audit.get("file_count"):
        raise ValueError("inventory and anchor audit file counts differ")
    if inventory.get("error_count"):
        raise ValueError("inventory contains unreadable PDFs")


def _initialize_database(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            relative_path TEXT PRIMARY KEY,
            exam_id TEXT NOT NULL UNIQUE,
            source_sha256 TEXT NOT NULL,
            audit_status TEXT NOT NULL,
            expected_question_count INTEGER NOT NULL,
            generation_status TEXT NOT NULL,
            question_images INTEGER NOT NULL DEFAULT 0,
            output_bytes INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            manifest_path TEXT,
            report_path TEXT,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _sync_queue(
    connection: sqlite3.Connection,
    inventory: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    inventory_by_path = {item["path"]: item for item in inventory["files"]}
    audit_by_path = {item["path"]: item for item in audit["files"]}
    if set(inventory_by_path) != set(audit_by_path):
        raise ValueError("inventory and audit path sets differ")
    now = _utc_now()
    for relative_path in sorted(inventory_by_path):
        item = inventory_by_path[relative_path]
        audited = audit_by_path[relative_path]
        source_sha = item.get("sha256")
        if not source_sha:
            raise ValueError(f"missing SHA-256 in inventory: {relative_path}")
        audit_status = audited["status"]
        initial_status = "queued" if audit_status == "proposal_candidate" else audit_status
        expected_count = int(audited.get("question_anchor_count", 0))
        existing = connection.execute(
            "SELECT generation_status FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        if existing is not None:
            previous_status = existing["generation_status"]
            if previous_status == "running":
                previous_status = "queued"
            if previous_status in TERMINAL_GENERATION_STATUSES or previous_status.startswith("failed"):
                initial_status = previous_status
        connection.execute(
            """
            INSERT INTO documents (
                relative_path, exam_id, source_sha256, audit_status,
                expected_question_count, generation_status, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                exam_id=excluded.exam_id,
                source_sha256=excluded.source_sha256,
                audit_status=excluded.audit_status,
                expected_question_count=excluded.expected_question_count,
                generation_status=excluded.generation_status,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                relative_path,
                stable_exam_id(relative_path, source_sha),
                source_sha,
                audit_status,
                expected_count,
                initial_status,
                now,
            ),
        )
    connection.commit()


def _mark_running(connection: sqlite3.Connection, relative_path: str) -> None:
    connection.execute(
        """
        UPDATE documents
        SET generation_status='running', attempts=attempts+1,
            error=NULL, updated_at_utc=?
        WHERE relative_path=?
        """,
        (_utc_now(), relative_path),
    )
    connection.commit()


def _mark_failed(
    connection: sqlite3.Connection,
    relative_path: str,
    status: str,
    error: str,
) -> None:
    connection.execute(
        """
        UPDATE documents
        SET generation_status=?, error=?, updated_at_utc=?
        WHERE relative_path=?
        """,
        (status, error[:4000], _utc_now(), relative_path),
    )
    connection.commit()


def _mark_generated(
    connection: sqlite3.Connection,
    relative_path: str,
    result: dict[str, Any],
) -> None:
    connection.execute(
        """
        UPDATE documents
        SET generation_status=?, question_images=?, output_bytes=?,
            warning_count=?, manifest_path=?, report_path=?, error=NULL,
            updated_at_utc=?
        WHERE relative_path=?
        """,
        (
            result["generation_status"],
            result["question_images"],
            result["output_bytes"],
            result["warning_count"],
            result["manifest_path"],
            result["report_path"],
            _utc_now(),
            relative_path,
        ),
    )
    connection.commit()


def _write_status_snapshot(
    connection: sqlite3.Connection,
    destination: Path,
    stopped_reason: str | None,
) -> dict[str, Any]:
    snapshot = _status_snapshot(connection, destination, stopped_reason)
    _write_json_atomic(destination / "_batch" / "status.json", snapshot)
    return snapshot


def _status_snapshot(
    connection: sqlite3.Connection,
    destination: Path,
    stopped_reason: str | None,
) -> dict[str, Any]:
    status_counts = {
        row["generation_status"]: row["count"]
        for row in connection.execute(
            """
            SELECT generation_status, COUNT(*) AS count
            FROM documents
            GROUP BY generation_status
            ORDER BY generation_status
            """
        )
    }
    totals = connection.execute(
        """
        SELECT COUNT(*) AS documents,
               COALESCE(SUM(question_images), 0) AS question_images,
               COALESCE(SUM(output_bytes), 0) AS output_bytes,
               COALESCE(SUM(warning_count), 0) AS warning_count,
               COALESCE(SUM(CASE WHEN generation_status LIKE 'failed%' THEN 1 ELSE 0 END), 0)
                    AS failed_documents
        FROM documents
        """
    ).fetchone()
    free_bytes = shutil.disk_usage(destination).free
    return {
        "schema_version": 1,
        "updated_at_utc": _utc_now(),
        "output_root": str(destination),
        "status_counts": status_counts,
        "document_count": totals["documents"],
        "question_image_count": totals["question_images"],
        "output_bytes": totals["output_bytes"],
        "warning_count": totals["warning_count"],
        "failed_document_count": totals["failed_documents"],
        "free_disk_bytes": free_bytes,
        "stopped_reason": stopped_reason,
        "body_text_ocr": False,
        "one_file_per_question_required": True,
    }


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("another batch generator is already running") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        stream.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ValidationFailure(RuntimeError):
    pass
