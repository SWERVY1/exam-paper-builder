import unittest
import tempfile
import sqlite3
from pathlib import Path

from exam_image_splitter.batch import (
    _next_unattempted_document,
    _quarantine_failed_output,
    stable_exam_id,
)


class BatchTests(unittest.TestCase):
    def test_exam_id_is_stable_but_path_specific(self) -> None:
        digest = "a" * 64
        first = stable_exam_id("folder/a.pdf", digest)
        self.assertEqual(first, stable_exam_id("folder/a.pdf", digest))
        self.assertNotEqual(first, stable_exam_id("folder/b.pdf", digest))
        self.assertTrue(first.startswith("doc_aaaaaaaaaaaa_"))

    def test_failed_output_is_moved_out_of_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failed = root / "staging" / "doc_test"
            failed.mkdir(parents=True)
            (failed / "build-report.json").write_text("{}", encoding="utf-8")

            quarantined = _quarantine_failed_output(root, "doc_test")

            self.assertIsNotNone(quarantined)
            assert quarantined is not None
            self.assertFalse(failed.exists())
            self.assertTrue((quarantined / "build-report.json").is_file())

    def test_retry_does_not_loop_a_failed_document_within_one_run(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE documents (relative_path TEXT, generation_status TEXT)"
            )
            connection.executemany(
                "INSERT INTO documents VALUES (?, ?)",
                [("a.pdf", "failed_validation"), ("b.pdf", "failed_validation")],
            )
            attempted = {"a.pdf"}

            row = _next_unattempted_document(
                connection, ["failed_validation"], attempted
            )

            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["relative_path"], "b.pdf")
            attempted.add("b.pdf")
            self.assertIsNone(
                _next_unattempted_document(
                    connection, ["failed_validation"], attempted
                )
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
