"""Create copyright-free synthetic assets and a composition manifest."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
ASSET_DIR = ROOT / "demo-assets"
MANIFEST_PATH = ROOT / "demo-manifest.json"


def find_font() -> Path:
    candidates = [
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Set a usable TrueType font path in make_demo_assets.py.")


def draw_asset(path: Path, title: str, *, source_number: int | None, lines: int) -> None:
    width = 1200
    height = 220 + lines * 58
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font_path = find_font()
    number_font = ImageFont.truetype(str(font_path), 64)
    body_font = ImageFont.truetype(str(font_path), 34)
    if source_number is not None:
        draw.text((28, 22), f"{source_number}.", font=number_font, fill="black")
    draw.text((210 if source_number is not None else 32, 42), title, font=body_font, fill="black")
    for index in range(lines):
        y = 155 + index * 58
        right = 1110 if index % 3 else 980
        draw.line((54, y, right, y), fill=(55, 55, 55), width=4)
    image.save(path)
    image.close()


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    font_path = find_font()
    draw_asset(ASSET_DIR / "stimulus.png", "Synthetic shared passage", source_number=None, lines=34)
    draw_asset(ASSET_DIR / "question-17.png", "Synthetic question A", source_number=17, lines=8)
    draw_asset(ASSET_DIR / "question-18.png", "Synthetic question B", source_number=18, lines=9)
    draw_asset(ASSET_DIR / "solution-17.png", "Synthetic explanation A", source_number=None, lines=6)
    draw_asset(ASSET_DIR / "solution-18.png", "Synthetic explanation B", source_number=None, lines=7)

    manifest = {
        "version": 1,
        "build_id": "synthetic-demo",
        "title": "Synthetic Mock Exam",
        "subject": "demo",
        "font_path": str(font_path),
        "source_label": {"mode": "none"},
        "paper": {
            "size": "A4",
            "orientation": "portrait",
            "columns": 2,
            "flow_mode": "continuous-strip",
            "margins_mm": {"top": 12, "right": 12, "bottom": 12, "left": 12},
            "gutter_mm": 8,
            "header_mm": 12,
            "footer_mm": 7,
            "asset_gap_mm": 3,
            "min_effective_dpi": 120,
            "min_scale_fraction": 0.70,
            "qa_dpi": 100,
        },
        "answer": {"mode": "key-and-solutions"},
        "stimuli": [
            {
                "id": "DEMO_BUNDLE_S01",
                "assets": [
                    {
                        "path": "demo-assets/stimulus.png",
                        "break_policy": "flow",
                        "safe_breaks": [0.31, 0.58, 0.82],
                    }
                ],
            }
        ],
        "questions": [
            {
                "id": "DEMO_Q017",
                "bundle_id": "DEMO_BUNDLE",
                "original_number": 17,
                "stimulus_ids": ["DEMO_BUNDLE_S01"],
                "assets": [{"path": "demo-assets/question-17.png", "break_policy": "flow"}],
                "answer": "2",
                "solution_assets": ["demo-assets/solution-17.png"],
                "renumber": {
                    "mode": "mask",
                    "asset_index": 0,
                    "bbox": [0.01, 0.01, 0.16, 0.16],
                    "number_font_path": str(font_path),
                },
                "solution_renumber": {"mode": "source-number-absent", "asset_index": 0},
            },
            {
                "id": "DEMO_Q018",
                "bundle_id": "DEMO_BUNDLE",
                "original_number": 18,
                "stimulus_ids": ["DEMO_BUNDLE_S01"],
                "assets": [{"path": "demo-assets/question-18.png", "break_policy": "safe-only", "safe_breaks": [0.62]}],
                "answer": "4",
                "solution_assets": ["demo-assets/solution-18.png"],
                "renumber": {
                    "mode": "mask",
                    "asset_index": 0,
                    "bbox": [0.01, 0.01, 0.16, 0.16],
                    "number_font_path": str(font_path),
                },
                "solution_renumber": {"mode": "source-number-absent", "asset_index": 0},
            },
        ],
        "selection": [
            {"question_id": "DEMO_Q017", "new_number": 1},
            {"question_id": "DEMO_Q018", "new_number": 2},
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(MANIFEST_PATH)


if __name__ == "__main__":
    main()

