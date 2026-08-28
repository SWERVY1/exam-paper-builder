from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Iterable

import pypdfium2 as pdfium
from PIL import Image


class PdfRenderer:
    def __init__(
        self, source_pdf: str | Path, dpi: int = 300, cache_pages: int = 2
    ) -> None:
        self.source_pdf = Path(source_pdf).resolve()
        self.dpi = dpi
        self.cache_pages = max(1, cache_pages)
        self._document = pdfium.PdfDocument(str(self.source_pdf))
        self._cache: OrderedDict[int, Image.Image] = OrderedDict()

    @property
    def page_count(self) -> int:
        return len(self._document)

    def page_size_points(self, page_number: int) -> tuple[float, float]:
        self._check_page(page_number)
        page = self._document[page_number - 1]
        try:
            width, height = page.get_size()
            return float(width), float(height)
        finally:
            page.close()

    def render_page(self, page_number: int) -> Image.Image:
        self._check_page(page_number)
        cached = self._cache.get(page_number)
        if cached is not None:
            self._cache.move_to_end(page_number)
            return cached

        page = self._document[page_number - 1]
        bitmap = None
        try:
            bitmap = page.render(scale=self.dpi / 72.0)
            image = bitmap.to_pil().convert("RGB")
        finally:
            if bitmap is not None:
                bitmap.close()
            page.close()

        self._cache[page_number] = image
        while len(self._cache) > self.cache_pages:
            _, evicted = self._cache.popitem(last=False)
            evicted.close()
        return image

    def crop(self, page_number: int, bbox: Iterable[float]) -> Image.Image:
        page = self.render_page(page_number)
        x0, y0, x1, y1 = (float(value) for value in bbox)
        width, height = page.size
        left = max(0, min(width - 1, round(x0 * width)))
        top = max(0, min(height - 1, round(y0 * height)))
        right = max(left + 1, min(width, round(x1 * width)))
        bottom = max(top + 1, min(height, round(y1 * height)))
        return page.crop((left, top, right, bottom))

    def close(self) -> None:
        for image in self._cache.values():
            image.close()
        self._cache.clear()
        self._document.close()

    def __enter__(self) -> "PdfRenderer":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _check_page(self, page_number: int) -> None:
        if page_number < 1 or page_number > self.page_count:
            raise IndexError(f"Page {page_number} is outside 1..{self.page_count}")

