"""Local page reading: PDF text layer when present, otherwise Tesseract OCR. Nothing leaves the server here."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image, ImageOps

RENDER_DPI = 200


class OCRUnavailable(RuntimeError):
    pass


def _find_tesseract() -> None:
    """Use Tesseract from PATH, or from its usual Windows install folder."""
    import os
    import shutil

    if shutil.which("tesseract"):
        return
    for candidate in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                      os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")):
        if os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


_find_tesseract()
MIN_TEXT_CHARS = 25  # a page with less text-layer text than this is treated as scanned


@dataclass
class Line:
    page: int
    line_no: int
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of page width/height
    source: str  # 'text_layer' | 'ocr'


@dataclass
class ReadResult:
    lines: list[Line]
    page_images: list[Path]
    has_text_layer: bool


def _ocr_image(img: Image.Image, page: int) -> list[Line]:
    img = ImageOps.exif_transpose(img).convert("L")
    w, h = img.size
    try:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 4")
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailable("Local OCR (Tesseract) is not installed on this server, so photos and scanned pages "
                             "can't be read. PDFs with a text layer still work.") from exc
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        try:
            if float(data["conf"][i]) < 0:
                continue
        except ValueError:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(i)
    out: list[Line] = []
    ordered = sorted(groups.values(), key=lambda idx: (min(data["top"][i] for i in idx), min(data["left"][i] for i in idx)))
    for idx in ordered:
        idx.sort(key=lambda i: data["left"][i])
        text = " ".join(data["text"][i].strip() for i in idx)
        x0 = min(data["left"][i] for i in idx)
        y0 = min(data["top"][i] for i in idx)
        x1 = max(data["left"][i] + data["width"][i] for i in idx)
        y1 = max(data["top"][i] + data["height"][i] for i in idx)
        out.append(Line(page, len(out) + 1, text, (x0 / w, y0 / h, x1 / w, y1 / h), "ocr"))
    return out


def _text_layer_lines(page: pymupdf.Page, page_no: int) -> list[Line]:
    pw, ph = page.rect.width, page.rect.height
    raw: list[tuple[float, float, str, tuple[float, float, float, float]]] = []
    for block in page.get_text("dict")["blocks"]:
        for ln in block.get("lines", []):
            text = "".join(s["text"] for s in ln["spans"]).strip()
            if not text:
                continue
            x0, y0, x1, y1 = ln["bbox"]
            raw.append((round(y0, 0), x0, text, (x0 / pw, y0 / ph, x1 / pw, y1 / ph)))
    raw.sort(key=lambda r: (r[0], r[1]))
    # merge spans that sit on the same baseline into one reading line
    merged: list[tuple[str, tuple[float, float, float, float]]] = []
    last_y = None
    for y, _x, text, bb in raw:
        if merged and last_y is not None and abs(y - last_y) <= 2:
            prev_text, pb = merged[-1]
            merged[-1] = (prev_text + "  " + text, (min(pb[0], bb[0]), min(pb[1], bb[1]), max(pb[2], bb[2]), max(pb[3], bb[3])))
        else:
            merged.append((text, bb))
        last_y = y
    return [Line(page_no, i + 1, t, bb, "text_layer") for i, (t, bb) in enumerate(merged)]


def read_document(path: Path, out_dir: Path) -> ReadResult:
    """Read a PDF or image into lines with positions, and save a PNG of every page for the review screen."""
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    lines: list[Line] = []
    images: list[Path] = []
    any_text_layer = False
    if suffix == ".pdf":
        doc = pymupdf.open(path)
        try:
            for i, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=RENDER_DPI)
                img_path = out_dir / f"page-{i}.png"
                pix.save(img_path)
                images.append(img_path)
                tl = _text_layer_lines(page, i)
                if sum(len(l.text) for l in tl) >= MIN_TEXT_CHARS:
                    any_text_layer = True
                    lines.extend(tl)
                else:
                    lines.extend(_ocr_image(Image.open(io.BytesIO(pix.tobytes("png"))), i))
        finally:
            doc.close()
    else:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img_path = out_dir / "page-1.png"
        img.convert("RGB").save(img_path)
        images.append(img_path)
        lines.extend(_ocr_image(img, 1))
    return ReadResult(lines=lines, page_images=images, has_text_layer=any_text_layer)


def masked_page_png(page_png: Path, boxes: list[tuple[float, float, float, float]]) -> bytes:
    """Black out the given regions of a page image (used before the opt-in vision fallback)."""
    from PIL import ImageDraw

    img = Image.open(page_png).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle([x0 * w - 4, y0 * h - 4, x1 * w + 4, y1 * h + 4], fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
