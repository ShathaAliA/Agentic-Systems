from pathlib import Path
import csv
from openpyxl import load_workbook
from magika import Magika
import pymupdf
from PIL import Image
from docx import Document
from pptx import Presentation
import json
import pytesseract
import os
import uuid

detector = None


def get_detector():
    global detector
    if detector is None:
        detector = Magika()
    return detector

SUPPORTED_TYPES = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".md": "markdown",
    ".csv": "csv",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

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

    if extension.lower() == ".pptx" and detected_type in ("pptx", "presentation", "powerpoint", "zip"):
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
                if doc.page_count > 100:
                    return {"is_valid": False, "status": "too_many_pages"}
                for page_number in range(doc.page_count):
                    page = doc.load_page(page_number)
                    _ = page.rect

        elif file_type == "doc":
            from tika import parser
            parsed = parser.from_file(str(path))
            if not isinstance(parsed, dict) or parsed.get("status", 200) != 200:
                raise ValueError("Invalid DOC")

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
                for line in file: pass

        elif file_type == "csv":
            with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as file:
                reader = csv.reader(file, strict=True)
                expected_columns = None
                for row in reader:
                    if expected_columns is None: expected_columns = len(row)
                    elif len(row) != expected_columns: raise ValueError("Inconsistent columns")

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
        return {"is_valid": False, "status": "invalid_format", "file_type": file_type, "error": str(error)}

def extract_content(file_path, file_type):
    path = Path(file_path)

    # ---------- PDF (مع رقم الصفحة) ----------
    if file_type == "pdf":
        pages = []
        all_text = []
        with pymupdf.open(str(path)) as doc:
            for page_index, page in enumerate(doc):
                text = page.get_text("text", sort=True)
                if text:
                    pages.append({
                        "page_number": page_index + 1,
                        "text": text
                    })
                    all_text.append(text)

        return {
            "status": "extracted",
            "original_text": "\n".join(all_text),
            "pages": pages
        }

    # ---------- DOCX (مع Markdown للجداول) ----------
    elif file_type == "docx":
        document = Document(str(path))
        text_parts = []

        for item in document.iter_inner_content():
            if hasattr(item, "rows"):
                # تحويل الجدول إلى Markdown
                table_md = []
                for row in item.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    table_md.append("| " + " | ".join(cells) + " |")
                # إضافة صف الفواصل بعد العنوان (إذا حبيتي)
                if table_md:
                    header_sep = "|" + "|".join(["---"] * len(item.rows[0].cells)) + "|"
                    table_md.insert(1, header_sep)
                text_parts.append("\n".join(table_md))
            else:
                text_parts.append(item.text)

        full_text = "\n".join(text_parts)
        return {
            "status": "extracted",
            "original_text": full_text,
            "pages": [{"page_number": 1, "text": full_text}]
        }

    # ---------- PPTX (مع OCR للصور) ----------
    elif file_type == "pptx":
        prs = Presentation(str(path))
        slides_data = []
        all_text = []

        for slide_num, slide in enumerate(prs.slides, start=1):
            slide_texts = []

            for shape in slide.shapes:
                # 1. نص عادي
                if hasattr(shape, "text") and shape.text.strip():
                    slide_texts.append(shape.text.strip())

                # 2. صورة (OCR)
                elif shape.shape_type == 13: # PICTURE
                    try:
                        image = shape.image
                        image_bytes = image.blob
                        temp_path = f"temp_{uuid.uuid4()}.png"

                        with open(temp_path, "wb") as f:
                            f.write(image_bytes)

                        ocr_text = pytesseract.image_to_string(Image.open(temp_path), lang='ara+eng')
                        if ocr_text.strip():
                            slide_texts.append(f"[صورة]: {ocr_text.strip()}")

                        os.remove(temp_path)
                    except Exception as e:
                        print(f"OCR Error: {e}")

            slide_full_text = "\n".join(slide_texts)
            if slide_full_text:
                slides_data.append({
                    "page_number": slide_num,
                    "text": slide_full_text
                })
                all_text.append(f"--- Slide {slide_num} ---\n{slide_full_text}")

        return {
            "status": "extracted",
            "original_text": "\n\n".join(all_text),
            "pages": slides_data
        }

    # ---------- MARKDOWN ----------
    elif file_type == "markdown":
        text = path.read_text(encoding="utf-8-sig")
        return {
            "status": "extracted",
            "original_text": text,
            "pages": [{"page_number": 1, "text": text}]
        }

    # ---------- CSV ----------
    elif file_type == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file, strict=True)
            rows = list(reader)
        text = "\n".join(",".join(row) for row in rows)
        return {
            "status": "extracted",
            "original_text": text,
            "pages": [{"page_number": 1, "text": text}]
        }

    # ---------- XLSX (مع رقم الشيت) ----------
    elif file_type == "xlsx":
        workbook = load_workbook(str(path), read_only=True, data_only=True)
        sheets = []
        all_text = []
        try:
            for sheet_index, worksheet in enumerate(workbook.worksheets, start=1):
                rows = []
                for row in worksheet.iter_rows(values_only=True):
                    values = [str(value) if value is not None else "" for value in row]
                    rows.append(values)
                    all_text.append(" | ".join(values))
                sheets.append({
                    "page_number": sheet_index,
                    "text": "\n".join(" | ".join(row) for row in rows)
                })
        finally:
            workbook.close()
        return {
            "status": "extracted",
            "original_text": "\n".join(all_text),
            "pages": sheets
        }

    # ---------- PNG / JPEG (OCR للصورة) ----------
    elif file_type in ("png", "jpeg"):
        try:
            ocr_text = pytesseract.image_to_string(Image.open(path), lang='ara+eng')
            return {
                "status": "extracted",
                "original_text": ocr_text.strip(),
                "pages": [{"page_number": 1, "text": ocr_text.strip()}]
            }
        except Exception as e:
            return {
                "status": "no_text_extraction_without_ocr",
                "original_text": None,
                "pages": [],
                "error": str(e)
            }

    else:
        raise ValueError(f"No extractor available for: {file_type}")

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
        return {"status": "extraction_failed", "file_name": detection["file_name"], "error": str(error)}

    return {
        "status": "accepted" if extraction["status"] == "extracted" else "accepted_no_text_extraction",
        "file_name": detection["file_name"],
        "file_type": detection["file_type"],
        "mime_type": detection["mime_type"],
        "confidence": detection["confidence"],
        "extraction": extraction
    }
