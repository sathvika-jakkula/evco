"""Local, header-only recovery for image-backed PDFs with existing row text.

Never OCR prices/quantities or infer labels from values. Candidate labels still
have to pass the parser's normal required-column recognition before use.
"""
from pathlib import Path
import re
from typing import List

import fitz
from PIL import Image, ImageOps

_TESSDATA = Path(__file__).with_name("tessdata")
_MAX_IMAGE_EDGE = 2200


def _groups(positions, gap=2):
    groups = []
    for position in positions:
        if groups and position <= groups[-1][-1] + gap:
            groups[-1].append(position)
        else:
            groups.append([position])
    return groups


def _header_bands(gray):
    """Locate broad dark header fills; color does not encode business meaning."""
    occupied = (
        y for y in range(int(gray.height * 0.15), gray.height)
        if sum(gray.crop((0, y, gray.width, y + 1)).histogram()[:180])
        > gray.width * 0.5
    )
    return [(group[0], group[-1] + 1) for group in _groups(occupied)
            if 12 <= len(group) <= gray.height * 0.12]


def _column_edges(gray, top, bottom):
    """Use the long vertical rules below a header, not OCR word positions.

    Some source text layers are reflowed at the left margin and cannot provide
    coordinates for the visible grid. Require at least seven physical cells.
    """
    sample_bottom = min(gray.height, bottom + int((bottom - top) * 2.6))
    height = sample_bottom - bottom
    if height < 40:
        return []
    positions = (
        x for x in range(gray.width)
        if sum(gray.crop((x, bottom, x + 1, sample_bottom)).histogram()[:100])
        > height * 0.75
    )
    edges = [round(sum(group) / len(group)) for group in _groups(positions)]
    if not 8 <= len(edges) <= 25 or edges[-1] - edges[0] < gray.width * 0.5:
        return []
    if any(right - left < 20 for left, right in zip(edges, edges[1:])):
        return []
    return edges


def _ocr_header_cell(gray, box):
    # White-on-dark table labels need inversion. Remove ruling lines before
    # OCR, and pad each isolated cell so wrapped labels remain one column.
    crop = ImageOps.invert(gray.crop(box)).point(lambda value: 0 if value < 100 else 255)
    crop = ImageOps.expand(crop.convert("RGB"), border=20, fill="white")
    pix = fitz.Pixmap(fitz.csRGB, crop.width, crop.height, crop.tobytes(), False)
    with fitz.open(stream=pix.pdfocr_tobytes(language="eng", tessdata=str(_TESSDATA))) as doc:
        return " ".join(doc[0].get_text().split())


def recover_header_candidates(page) -> List[dict]:
    """Return OCR labels only for a narrowly identified incomplete-text PDF.

    Pure scans remain unsupported: existing text must contain a BOM-shaped
    identifier and a printed price. Ordinary digital pages are also excluded.
    Rendering is bounded and at most three candidate header bands are OCRed.
    """
    text = page.get_text("text")
    if (not re.search(r"\b\d{4,}\s*/\s*\d{5,}\b", text)
            or not re.search(r"\$\s*\d+[.,]\d{2}", text)):
        return []
    if not any((fitz.Rect(info["bbox"]) & page.rect).get_area() >= page.rect.get_area() * 0.8
               for info in page.get_image_info()):
        return []
    if page.find_tables().tables:
        return []
    if not (_TESSDATA / "eng.traineddata").is_file():
        raise RuntimeError("Bundled English header OCR data is missing")

    scale = _MAX_IMAGE_EDGE / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    gray = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    candidates = []
    for top, bottom in _header_bands(gray)[:3]:
        edges = _column_edges(gray, top, bottom)
        if not edges:
            continue
        labels = [_ocr_header_cell(gray, (left + 3, top + 2, right - 3, bottom - 2))
                  for left, right in zip(edges, edges[1:])]
        candidates.append({"labels": labels, "pixel_band": [top, bottom], "column_edges": edges})
    return candidates
