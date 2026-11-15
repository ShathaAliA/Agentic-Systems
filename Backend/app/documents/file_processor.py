"""File detection, validation, extraction, and normalization.

Supports: PDF (digital + scanned via OCR), DOCX, PPTX, MD, CSV, XLSX, PNG, JPEG.
Returns extraction with real locations (page/slide/sheet).
"""
from pathlib import Path
import csv
import json
import logging
import os
import uuid
from typing import Any

from openpyxl import load_workbook
from magika import Magika
import pymupdf
from PIL import Image
from docx import Document
from pptx import Presentation
import pytesseract

# ✅ Windows: explicit path (bypass PATH issues)
if os.name == "nt":
    _tess_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(_tess_path):
        pytesseract.pytesseract.tesseract_cmd = _tess_path

logger = logging.getLogger(__name__)

# Lazy Magika detector (avoid loading at import time)
_detector = None


def get_detector():
    global _detector
    if _detector is None:
        _detector = Magika()
    return _detector


SUPPORTED_TYPES = {
    ".pdf": "pdf",
    ".docx": "docx",
    # .doc (legacy) intentionally excluded (requires tika; not in MVP)
    ".md": "markdown",
    ".csv": "csv",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_PDF_PAGES = 100


def detect_file(file_path):
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError("File not found")
    if path.stat().st_size == 0:
        raise ValueError("File is empty")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError("File exceeds 20 MB limit")

    result = get_detector().identify_path(path)
    return {
        "file_name": path.name,
        "extension": path.suffix.lower(),
        "file_type": str(result.output.label),
        "mime_type": result.output.mime_type,
        "confidence": float(result.score),
    }


def validate_file(extension, detected_type):
    expected_type = SUPPORTED_TYPES.get(extension.lower())
    if expected_type is None:
        return {"is_supported": False, "status": "unsupported_file_type"}

    if extension.lower() == ".pptx" and detected_type in (
        "pptx", "presentation", "powerpoint", "zip"
    ):
        return {"is_supported": True, "status": "accepted_for_format_validation"}

    if expected_type != detected_type:
        return {
            "is_supported": False,
            "status": "extension_mismatch",
            "expected": expected_type,
            "detected": detected_type,
        }
    return {"is_supported": True, "status": "accepted_for_format_validation"}


def validate_format(file_path, file_type):
    path = Path(file_path)
    try:
        if file_type == "pdf":
            with pymupdf.open(str(path)) as doc:
                if not doc.is_pdf:
                    raise ValueError("Not a PDF")
                if doc.needs_pass:
                    return {"is_valid": False, "status": "password_required"}
                if doc.page_count > MAX_PDF_PAGES:
                    return {"is_valid": False, "status": "too_many_pages"}
                for page_number in range(doc.page_count):
                    page = doc.load_page(page_number)
                    _ = page.rect

        elif file_type == "docx":
            document = Document(str(path))
            for paragraph in document.paragraphs:
                _ = paragraph.text
            for table in document.tables:
                for row in table.rows:
                    for cell in row.cells:
                        _ = cell.text

        elif file_type == "pptx":
            prs = Presentation(str(path))
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        _ = shape.text

        elif file_type == "markdown":
            with path.open("r", encoding="utf-8-sig", errors="strict") as file:
                for line in file:
                    pass

        elif file_type == "csv":
            with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as file:
                reader = csv.reader(file, strict=True)
                expected_columns = None
                for row in reader:
                    if expected_columns is None:
                        expected_columns = len(row)
                    elif len(row) != expected_columns:
                        raise ValueError("Inconsistent columns")

        elif file_type in ("png", "jpeg"):
            expected_format = {"png": "PNG", "jpeg": "JPEG"}[file_type]
            with Image.open(str(path)) as image:
                if image.format != expected_format:
                    raise ValueError("Image format mismatch")
                image.verify()
            with Image.open(path) as image:
                image.load()

        elif file_type == "xlsx":
            workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
            try:
                if not workbook.sheetnames:
                    raise ValueError("Excel file has no sheets")
                for worksheet in workbook.worksheets:
                    for row in worksheet.iter_rows():
                        for cell in row:
                            _ = cell.value
            finally:
                workbook.close()

        return {"is_valid": True, "status": "valid_format", "file_type": file_type}

    except Exception as error:
        # Log internally; return safe status (no str(error) to caller)
        logger.warning("Format validation failed for %s: %s", file_type, error)
        return {"is_valid": False, "status": "invalid_format", "file_type": file_type}


def _ocr_image(path_or_image, lang="ara+eng"):
    """OCR helper — accepts path or PIL image. Returns stripped text."""
    return pytesseract.image_to_string(path_or_image, lang=lang).strip()


def _extract_pdf(path):
    """PDF extraction with OCR fallback for scanned pages."""
    pages = []
    all_text = []
    with pymupdf.open(str(path)) as doc:
        for page_index, page in enumerate(doc):
            text = page.get_text("text", sort=True)
            if not text or not text.strip():
                # OCR fallback for scanned PDF pages
                try:
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    ocr_text = _ocr_image(Image.open(__import__("io").BytesIO(img_bytes)))
                    if ocr_text:
                        text = ocr_text
                except Exception as e:
                    logger.warning("OCR failed for PDF page %d: %s", page_index + 1, e)
            if text and text.strip():
                pages.append({"page_number": page_index + 1, "text": text})
                all_text.append(text)

    return {
        "status": "extracted" if all_text else "no_text_extracted",
        "original_text": "\n".join(all_text),
        "pages": pages,
    }


def _extract_docx(path):
    document = Document(str(path))
    text_parts = []
    for item in document.iter_inner_content():
        if hasattr(item, "rows"):
            table_md = []
            for row in item.rows:
                cells = [cell.text.strip() for cell in row.cells]
                table_md.append("| " + " | ".join(cells) + " |")
            if table_md:
                header_sep = "|" + "|".join(["---"] * len(item.rows[0].cells)) + "|"
                table_md.insert(1, header_sep)
            text_parts.append("\n".join(table_md))
        else:
            text_parts.append(item.text)

    full_text = "\n".join(text_parts)
    return {
        "status": "extracted" if full_text.strip() else "no_text_extracted",
        "original_text": full_text,
        "pages": [{"page_number": 1, "text": full_text}],
    }


def _extract_pptx(path):
    prs = Presentation(str(path))
    slides_data = []
    all_text = []
    for slide_num, slide in enumerate(prs.slides, start=1):
        slide_texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                slide_texts.append(shape.text.strip())
            elif shape.shape_type == 13:  # PICTURE
                temp_path = None
                try:
                    image = shape.image
                    temp_path = f"temp_pptx_{uuid.uuid4()}.png"
                    with open(temp_path, "wb") as f:
                        f.write(image.blob)
                    ocr_text = _ocr_image(Image.open(temp_path))
                    if ocr_text:
                        slide_texts.append(f"[صورة]: {ocr_text}")
                except Exception as e:
                    logger.warning("OCR failed for PPTX slide %d: %s", slide_num, e)
                finally:
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass

        slide_full_text = "\n".join(slide_texts)
        if slide_full_text.strip():
            slides_data.append({"page_number": slide_num, "text": slide_full_text})
            all_text.append(f"--- Slide {slide_num} ---\n{slide_full_text}")

    return {
        "status": "extracted" if all_text else "no_text_extracted",
        "original_text": "\n\n".join(all_text),
        "pages": slides_data,
    }


def _extract_markdown(path):
    text = path.read_text(encoding="utf-8-sig")
    return {
        "status": "extracted" if text.strip() else "no_text_extracted",
        "original_text": text,
        "pages": [{"page_number": 1, "text": text}],
    }


def _extract_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, strict=True)
        rows = list(reader)
    text = "\n".join(",".join(row) for row in rows)
    return {
        "status": "extracted" if text.strip() else "no_text_extracted",
        "original_text": text,
        "pages": [{"page_number": 1, "text": text}],
    }


def _extract_xlsx(path):
    workbook = load_workbook(str(path), read_only=True, data_only=True)
    sheets = []
    all_text = []
    try:
        for sheet_index, worksheet in enumerate(workbook.worksheets, start=1):
            rows = []
            for row in worksheet.iter_rows(values_only=True):
                values = [str(v) if v is not None else "" for v in row]
                rows.append(values)
                all_text.append(" | ".join(values))
            sheets.append({
                "page_number": sheet_index,
                "text": "\n".join(" | ".join(row) for row in rows),
            })
    finally:
        workbook.close()

    return {
        "status": "extracted" if all_text else "no_text_extracted",
        "original_text": "\n".join(all_text),
        "pages": sheets,
    }


def _extract_image(path):
    try:
        ocr_text = _ocr_image(Image.open(path))
        if ocr_text:
            return {
                "status": "extracted",
                "original_text": ocr_text,
                "pages": [{"page_number": 1, "text": ocr_text}],
            }
        return {
            "status": "no_text_extracted",
            "original_text": "",
            "pages": [],
        }
    except Exception as e:
        logger.warning("Image OCR failed: %s", e)
        return {
            "status": "no_text_extraction_without_ocr",
            "original_text": "",
            "pages": [],
        }


_EXTRACTORS = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "pptx": _extract_pptx,
    "markdown": _extract_markdown,
    "csv": _extract_csv,
    "xlsx": _extract_xlsx,
    "png": _extract_image,
    "jpeg": _extract_image,
}


def extract_content(file_path, file_type):
    extractor = _EXTRACTORS.get(file_type)
    if extractor is None:
        raise ValueError(f"No extractor available for: {file_type}")
    return extractor(Path(file_path))


def process_file(file_path):
    detection = detect_file(file_path)
    type_validation = validate_file(detection["extension"], detection["file_type"])
    if not type_validation["is_supported"]:
        return {"status": "rejected", "reason": type_validation["status"]}

    format_validation = validate_format(file_path, detection["file_type"])
    if not format_validation["is_valid"]:
        return {"status": "rejected", "reason": format_validation["status"]}

    try:
        extraction = extract_content(file_path, detection["file_type"])
    except Exception as error:
        logger.exception("Extraction failed for %s", detection["file_name"])
        return {
            "status": "extraction_failed",
            "file_name": detection["file_name"],
        }

    # ✅ Reject empty extraction (prevent false success)
    extracted_text = (extraction.get("original_text") or "").strip()
    if not extracted_text:
        return {
            "status": "rejected",
            "reason": "empty_extraction",
            "file_name": detection["file_name"],
            "file_type": detection["file_type"],
        }

    return {
        "status": "accepted",
        "file_name": detection["file_name"],
        "file_type": detection["file_type"],
        "mime_type": detection["mime_type"],
        "confidence": detection["confidence"],
        "extraction": extraction,
    }