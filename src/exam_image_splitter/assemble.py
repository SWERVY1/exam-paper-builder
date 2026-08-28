from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageColor


@dataclass(frozen=True)
class AssemblyConfig:
    output_width_px: int = 1800
    padding_px: int = 60
    gap_px: int = 36
    background: str = "#FFFFFF"
    max_output_height_px: int = 24000

    @property
    def content_width(self) -> int:
        return self.output_width_px - self.padding_px * 2


def assemble_vertical(
    pieces: list[Image.Image], config: AssemblyConfig
) -> list[Image.Image]:
    if not pieces:
        raise ValueError("At least one image piece is required.")

    normalized: list[Image.Image] = []
    for piece in pieces:
        if piece.width <= 0 or piece.height <= 0:
            raise ValueError("Image piece has invalid dimensions.")
        target_height = max(1, round(piece.height * config.content_width / piece.width))
        if piece.width == config.content_width:
            normalized.append(piece.copy())
        else:
            normalized.append(
                piece.resize(
                    (config.content_width, target_height), Image.Resampling.LANCZOS
                )
            )

    total_height = (
        config.padding_px * 2
        + sum(piece.height for piece in normalized)
        + config.gap_px * (len(normalized) - 1)
    )
    background = ImageColor.getrgb(config.background)
    canvas = Image.new("RGB", (config.output_width_px, total_height), background)
    y = config.padding_px
    for piece in normalized:
        canvas.paste(piece, (config.padding_px, y))
        y += piece.height + config.gap_px
        piece.close()

    maximum = config.max_output_height_px
    if maximum <= 0 or canvas.height <= maximum:
        return [canvas]
    parts = _split_at_light_rows(canvas, maximum, background)
    canvas.close()
    return parts


def _split_at_light_rows(
    image: Image.Image,
    maximum_height: int,
    background: tuple[int, int, int],
) -> list[Image.Image]:
    gray = image.convert("L").resize((1, image.height), Image.Resampling.BOX)
    if hasattr(gray, "get_flattened_data"):
        row_brightness = list(gray.get_flattened_data())
    else:
        row_brightness = list(gray.getdata())
    gray.close()

    parts: list[Image.Image] = []
    start = 0
    minimum_chunk = max(128, int(maximum_height * 0.65))
    search_window = min(240, int(maximum_height * 0.12))
    while image.height - start > maximum_height:
        target = start + maximum_height
        search_low = max(start + minimum_chunk, target - search_window)
        candidates = range(search_low, target + 1)
        cut = max(candidates, key=lambda row: (row_brightness[row - 1], row))
        crop = image.crop((0, start, image.width, cut))
        part = Image.new("RGB", crop.size, background)
        part.paste(crop, (0, 0))
        crop.close()
        parts.append(part)
        start = cut

    crop = image.crop((0, start, image.width, image.height))
    final = Image.new("RGB", crop.size, background)
    final.paste(crop, (0, 0))
    crop.close()
    parts.append(final)
    return parts
