from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


class WindowsOcrUnavailable(RuntimeError):
    """Raised when the local Windows OCR engine cannot provide safe anchors."""


_NUMBER_GLYPH = re.compile(r"^[\"'‘’]?(?:[0-9]{1,3}|[〇一二三四五六七八九十]+)[.·•]?$")


_WINDOWS_OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1
})[0]
function Await-WinRt($Operation, [Type]$ResultType) {
  $task = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
  $task.GetAwaiter().GetResult()
}
$fileType = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$streamType = [Windows.Storage.Streams.IRandomAccessStreamWithContentType,Windows.Storage.Streams,ContentType=WindowsRuntime]
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$resultType = [Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime]
$languageType = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$engineType = [Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]
$engine = $engineType::TryCreateFromLanguage($languageType::new('ko-KR'))
if ($null -eq $engine) { throw 'The local Korean Windows OCR language pack is unavailable.' }
$inputDir = [Environment]::GetEnvironmentVariable('EXAM_IMAGE_SPLITTER_OCR_INPUT')
$records = New-Object System.Collections.Generic.List[object]
Get-ChildItem -LiteralPath $inputDir -Filter 'page_*.png' | Sort-Object Name | ForEach-Object {
  $page = [int]($_.BaseName.Substring(5))
  $file = Await-WinRt ($fileType::GetFileFromPathAsync($_.FullName)) $fileType
  $stream = Await-WinRt ($file.OpenReadAsync()) $streamType
  $decoder = Await-WinRt ($decoderType::CreateAsync($stream)) $decoderType
  $bitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) $bitmapType
  $result = Await-WinRt ($engine.RecognizeAsync($bitmap)) $resultType
  foreach ($line in $result.Lines) {
    foreach ($word in $line.Words) {
      $rect = $word.BoundingRect
      $x = $rect.X / $bitmap.PixelWidth
      $y = $rect.Y / $bitmap.PixelHeight
      if ($y -ge 0.13 -and $y -le 0.86 -and (
        ($x -ge 0.090 -and $x -le 0.110) -or ($x -ge 0.495 -and $x -le 0.521)
      )) {
        $records.Add([pscustomobject]@{
          page = $page
          text = $word.Text
          x = $x
          y = $y
          width = $rect.Width / $bitmap.PixelWidth
          height = $rect.Height / $bitmap.PixelHeight
        })
      }
    }
  }
  $bitmap.Dispose()
  $stream.Dispose()
}
$records | ConvertTo-Json -Compress
"""


def detect_question_anchors_with_windows_ocr(
    source_pdf: str | Path,
    *,
    dpi: int = 150,
    expected_counts: tuple[int, ...] = (20, 30),
) -> list[dict[str, Any]]:
    """Return a complete 1-20 or 1-30 anchor sequence from an image-only PDF.

    OCR text is used only in memory to locate candidate number glyphs. The
    returned anchors contain geometry and sequence numbers, never OCR body text.
    """

    if os.name != "nt":
        raise WindowsOcrUnavailable("Windows OCR fallback is only available on Windows.")
    if dpi <= 0:
        raise ValueError("OCR DPI must be positive.")
    expected_counts = tuple(sorted({int(value) for value in expected_counts}))
    if not expected_counts or any(value <= 0 for value in expected_counts):
        raise ValueError("expected_counts must contain positive integers.")

    source = Path(source_pdf).resolve()
    retry_dpis = (dpi, 200) if dpi < 200 else (dpi,)
    last_error: WindowsOcrUnavailable | None = None
    for render_dpi in retry_dpis:
        try:
            return _detect_question_anchors_at_dpi(
                source, render_dpi, expected_counts=expected_counts
            )
        except WindowsOcrUnavailable as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _detect_question_anchors_at_dpi(
    source: Path,
    dpi: int,
    *,
    expected_counts: tuple[int, ...],
) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="exam-ocr-") as temporary:
        image_dir = Path(temporary)
        document = pdfium.PdfDocument(str(source))
        try:
            for page_index in range(len(document)):
                page = document[page_index]
                try:
                    image = page.render(scale=dpi / 72).to_pil()
                    try:
                        image.save(image_dir / f"page_{page_index + 1:02d}.png")
                    finally:
                        image.close()
                finally:
                    page.close()
        finally:
            document.close()

        records = _run_windows_ocr(image_dir)
    return _anchors_from_ocr_records(records, expected_counts=expected_counts)


def _run_windows_ocr(image_dir: Path) -> list[dict[str, Any]]:
    encoded = base64.b64encode(
        _WINDOWS_OCR_SCRIPT.encode("utf-16le")
    ).decode("ascii")
    environment = os.environ.copy()
    environment["EXAM_IMAGE_SPLITTER_OCR_INPUT"] = str(image_dir)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise WindowsOcrUnavailable("Windows OCR could not be executed.") from exc
    payload = completed.stdout.strip()
    if not payload:
        raise WindowsOcrUnavailable("Windows OCR returned no candidate anchors.")
    parsed = json.loads(payload)
    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        raise WindowsOcrUnavailable("Windows OCR returned an invalid payload.")
    return [item for item in parsed if isinstance(item, dict)]


def _anchors_from_ocr_records(
    records: list[dict[str, Any]],
    *,
    expected_counts: tuple[int, ...] = (20, 30),
) -> list[dict[str, Any]]:
    """Accept only a complete, correctly recognized number sequence."""

    candidates = sorted(
        [item for item in records if _NUMBER_GLYPH.fullmatch(str(item.get("text", "")))],
        key=lambda item: (
            int(item["page"]),
            0 if float(item["x"]) < 0.5 else 1,
            float(item["y"]),
            float(item["x"]),
        ),
    )
    allowed_counts = tuple(sorted({int(value) for value in expected_counts}))
    if len(candidates) not in allowed_counts:
        expected_text = " or ".join(str(value) for value in allowed_counts)
        raise WindowsOcrUnavailable(
            "Windows OCR found "
            f"{len(candidates)} candidate number slots, not a complete {expected_text}."
        )

    recognized = [_parse_number_glyph(str(item.get("text", ""))) for item in candidates]
    expected = list(range(1, len(candidates) + 1))
    if recognized != expected:
        mismatch_index = next(
            index
            for index, (actual, wanted) in enumerate(zip(recognized, expected), start=1)
            if actual != wanted
        )
        raise WindowsOcrUnavailable(
            "Windows OCR number sequence is unsafe: slot "
            f"{mismatch_index} was recognized as {recognized[mismatch_index - 1]!r}, "
            f"expected {mismatch_index}."
        )

    anchors: list[dict[str, Any]] = []
    for expected_number, item in enumerate(candidates, start=1):
        x0 = float(item["x"])
        y0 = float(item["y"])
        x1 = x0 + max(0.001, float(item.get("width", 0.01)))
        y1 = y0 + max(0.001, float(item.get("height", 0.01)))
        anchors.append(
            {
                "type": "question_start",
                "page": int(item["page"]),
                "column": "left" if x0 < 0.5 else "right",
                "number": expected_number,
                "bbox": [
                    round(x0, 6),
                    round(y0, 6),
                    round(min(1.0, x1), 6),
                    round(min(1.0, y1), 6),
                ],
                "confidence": "needs_review",
                "source": "windows_ocr_geometry",
            }
        )
    return anchors


def _parse_number_glyph(text: str) -> int | None:
    normalized = text.strip().strip("\"'‘’").rstrip(".·•").strip()
    if normalized.isdigit():
        value = int(normalized)
        return value if value > 0 else None
    if not normalized or any(char not in "〇一二三四五六七八九十" for char in normalized):
        return None
    digits = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" not in normalized:
        value = int("".join(str(digits[char]) for char in normalized))
        return value if value > 0 else None
    if normalized.count("十") != 1:
        return None
    tens_text, ones_text = normalized.split("十")
    if len(tens_text) > 1 or len(ones_text) > 1:
        return None
    tens = 1 if not tens_text else digits.get(tens_text)
    ones = 0 if not ones_text else digits.get(ones_text)
    if tens is None or ones is None or tens <= 0:
        return None
    value = tens * 10 + ones
    return value if value > 0 else None
