from pathlib import Path
import csv
from openpyxl import load_workbook
from magika import Magika
import pymupdf
from PIL import Image
from docx import Document
from pptx import Presentation
import json

# ==========================================
# Configuration
# ==========================================

detector = Magika()

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

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ==========================================
# 1. File Detection - Google Magika
# ==========================================

def detect_file(file_path):
    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError("File not found")

    if path.stat().st_size == 0:
        raise ValueError("File is empty")

    if path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError("File exceeds 50 MB limit")

    result = detector.identify_path(path)

    return {
        "file_name": path.name,
        "extension": path.suffix.lower(),
        "file_type": str(result.output.label),
        "mime_type": result.output.mime_type,
        "confidence": float(result.score),
    }


# ==========================================
# 2. File Type Validation
# ==========================================

def validate_file(extension, detected_type):
    expected_type = SUPPORTED_TYPES.get(extension.lower())

    if expected_type is None:
        return {
            "is_supported": False,
            "status": "unsupported_file_type"
        }

    # حل مشكلة Magika مع ملفات البوربوينت
    if extension.lower() == ".pptx" and detected_type in ("pptx", "presentation", "powerpoint", "zip"):
        return {
            "is_supported": True,
            "status": "accepted_for_format_validation"
        }

    if expected_type != detected_type:
        return {
            "is_supported": False,
            "status": "extension_mismatch",
            "expected": expected_type,
            "detected": detected_type,
        }

    return {
        "is_supported": True,
        "status": "accepted_for_format_validation"
    }


# ==========================================
# 3. Format Validation
# ==========================================

def validate_format(file_path, file_type):
    path = Path(file_path)

    try:

        # ---------- PDF ----------
        if file_type == "pdf":
            with pymupdf.open(str(path)) as doc:
                if not doc.is_pdf:
                    raise ValueError("Not a PDF")

                if doc.needs_pass:
                    return {
                        "is_valid": False,
                        "status": "password_required"
                    }

                for page_number in range(doc.page_count):
                    page = doc.load_page(page_number)
                    _ = page.rect

        # ---------- DOC ----------
        elif file_type == "doc":
            try:
                from tika import parser
                parsed = parser.from_file(str(path))
            except Exception as error:
                return {
                    "is_valid": False,
                    "status": "doc_validation_failed",
                    "error": str(error)
                }

            if not isinstance(parsed, dict):
                raise ValueError("Tika returned invalid data")

            if parsed.get("status", 200) != 200:
                raise ValueError("Tika could not parse DOC")

        # ---------- DOCX ----------
        elif file_type == "docx":
            document = Document(str(path))

            for paragraph in document.paragraphs:
                _ = paragraph.text

            for table in document.tables:
                for row in table.rows:
                    for cell in row.cells:
                        _ = cell.text

        # ---------- PPTX ----------
        elif file_type == "pptx":
            try:
                prs = Presentation(str(path))
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text"):
                            _ = shape.text
            except Exception as error:
                raise ValueError(f"Invalid PPTX: {error}")

        # ---------- MARKDOWN ----------
        elif file_type == "markdown":
            with path.open(
                "r",
                encoding="utf-8-sig",
                errors="strict"
            ) as file:
                for line in file:
                    pass

        # ---------- CSV ----------
        elif file_type == "csv":
            with path.open(
                "r",
                encoding="utf-8-sig",
                errors="strict",
                newline=""
            ) as file:

                reader = csv.reader(file, strict=True)
                expected_columns = None
                row_count = 0

                for row in reader:
                    row_count += 1

                    if expected_columns is None:
                        expected_columns = len(row)

                    elif len(row) != expected_columns:
                        raise ValueError(
                            f"Inconsistent columns at row {row_count}"
                        )

                if row_count == 0:
                    raise ValueError("CSV has no rows")

        # ---------- PNG / JPEG ----------
        elif file_type in ("png", "jpeg"):
            expected_format = {"png": "PNG", "jpeg": "JPEG"}[file_type]

            with Image.open(str(path)) as image:
                if image.format != expected_format:
                    raise ValueError(
                        f"Image format mismatch: expected {expected_format}, got {image.format}"
                    )
                image.verify()

            # Reopen and decode image data after verify().
            with Image.open(path) as image:
                image.load()

        # ---------- XLSX ----------
        elif file_type == "xlsx":
            workbook = load_workbook(
                filename=str(path),
                read_only=True,
                data_only=True
            )

            try:
                if not workbook.sheetnames:
                    raise ValueError("Excel file has no sheets")

                for worksheet in workbook.worksheets:
                    for row in worksheet.iter_rows():
                        for cell in row:
                            _ = cell.value

            finally:
                workbook.close()

        else:
            return {
                "is_valid": False,
                "status": "unsupported_format"
            }

        return {
            "is_valid": True,
            "status": "valid_format",
            "file_type": file_type
        }

    except Exception as error:
        return {
            "is_valid": False,
            "status": "invalid_format",
            "file_type": file_type,
            "error": str(error)
        }

# ==========================================
# 4. Content Extraction (Without OCR)
# ==========================================

def extract_content(file_path, file_type):
    path = Path(file_path)

    # ---------- PDF ----------
    if file_type == "pdf":
        pages = []
        all_text = []

        with pymupdf.open(str(path)) as doc:
            for page_index, page in enumerate(doc):
                text = page.get_text("text", sort=True)

                blocks = []
                for block in page.get_text("blocks", sort=True):
                    if len(block) >= 7 and block[6] == 0:
                        block_text = block[4]
                        if block_text.strip():
                            blocks.append({
                                "text": block_text,
                                "bbox": list(block[:4])
                            })

                pages.append({
                    "page_number": page_index + 1,
                    "original_text": text,
                    "blocks": blocks
                })
                all_text.append(text)

        return {
            "status": "extracted",
            "original_text": "\n".join(all_text),
            "pages": pages
        }

    # ---------- DOC ----------
    elif file_type == "doc":
        from tika import parser

        parsed = parser.from_file(str(path))

        if not isinstance(parsed, dict):
            raise ValueError("Tika failed to parse DOC")

        if parsed.get("status", 200) != 200:
            raise ValueError("Tika returned a parsing error")

        text = parsed.get("content") or ""

        return {
            "status": "extracted",
            "original_text": text,
            "paragraphs": [
                p.strip()
                for p in text.split("\n\n")
                if p.strip()
            ],
            "pages": None
        }

    # ---------- DOCX ----------
    elif file_type == "docx":
        document = Document(str(path))

        elements = []
        paragraphs = []
        tables = []
        text_parts = []

        for item in document.iter_inner_content():

            if hasattr(item, "rows"):
                table_rows = []

                for row in item.rows:
                    cells = [
                        cell.text for cell in row.cells
                    ]
                    table_rows.append(cells)

                tables.append({
                    "table_number": len(tables) + 1,
                    "rows": table_rows
                })

                elements.append({
                    "type": "table",
                    "rows": table_rows
                })

                for row in table_rows:
                    text_parts.append(" | ".join(row))

            else:
                paragraph_text = item.text

                paragraphs.append({
                    "paragraph_number": len(paragraphs) + 1,
                    "text": paragraph_text,
                    "style": item.style.name
                })

                elements.append({
                    "type": "paragraph",
                    "text": paragraph_text,
                    "style": item.style.name
                })

                text_parts.append(paragraph_text)

        return {
            "status": "extracted",
            "original_text": "\n".join(text_parts),
            "paragraphs": paragraphs,
            "tables": tables,
            "elements": elements,
            "pages": None
        }

    # ---------- PPTX ----------
    elif file_type == "pptx":
        prs = Presentation(str(path))
        
        slides_data = []
        all_text = []
        
        for slide_num, slide in enumerate(prs.slides, start=1):
            slide_texts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_texts.append(shape.text.strip())
            
            slide_full_text = "\n".join(slide_texts)
            if slide_full_text:
                slides_data.append({
                    "slide_number": slide_num,
                    "text": slide_full_text
                })
                all_text.append(f"--- Slide {slide_num} ---\n{slide_full_text}")
        
        return {
            "status": "extracted",
            "original_text": "\n\n".join(all_text),
            "slides": slides_data,
            "pages": None
        }

    # ---------- MARKDOWN ----------
    elif file_type == "markdown":
        text = path.read_text(encoding="utf-8-sig")

        return {
            "status": "extracted",
            "original_text": text,
            "lines": text.splitlines(),
            "pages": None
        }

    # ---------- CSV ----------
    elif file_type == "csv":
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:
            reader = csv.reader(file, strict=True)
            rows = list(reader)

        return {
            "status": "extracted",
            "original_text": "\n".join(
                ",".join(row) for row in rows
            ),
            "rows": rows,
            "row_count": len(rows),
            "pages": None
        }

    # ---------- XLSX ----------
    elif file_type == "xlsx":
        workbook = load_workbook(
            str(path),
            read_only=True,
            data_only=True
        )

        sheets = []
        text_parts = []

        try:
            for worksheet in workbook.worksheets:
                rows = []

                for row in worksheet.iter_rows(
                    values_only=True
                ):
                    values = [
                        str(value) if value is not None else ""
                        for value in row
                    ]
                    rows.append(values)
                    text_parts.append(" | ".join(values))

                sheets.append({
                    "sheet_name": worksheet.title,
                    "rows": rows
                })

        finally:
            workbook.close()

        return {
            "status": "extracted",
            "original_text": "\n".join(text_parts),
            "sheets": sheets,
            "pages": None
        }

    # ---------- PNG / JPEG ----------
    elif file_type in ("png", "jpeg"):
        with Image.open(path) as image:
            return {
                "status": "no_text_extraction_without_ocr",
                "original_text": None,
                "image_info": {
                    "format": image.format,
                    "width": image.width,
                    "height": image.height,
                    "mode": image.mode
                },
                "pages": None
            }

    else:
        raise ValueError(
            f"No extractor available for: {file_type}"
        )


def process_file(file_path):

    # Stage 1: File Detection
    detection = detect_file(file_path)

    print("\n========== FILE DETECTION ==========")
    print(detection)

    # Stage 2: File Type Validation
    type_validation = validate_file(
        detection["extension"],
        detection["file_type"]
    )

    print("\n========== TYPE VALIDATION ==========")
    print(type_validation)

    if not type_validation["is_supported"]:
        return {
            "status": "rejected",
            "reason": type_validation["status"]
        }

    # Stage 3: Format Validation
    format_validation = validate_format(
        file_path,
        detection["file_type"]
    )

    print("\n========== FORMAT VALIDATION ==========")
    print(format_validation)

    if not format_validation["is_valid"]:
        return {
            "status": "rejected",
            "reason": format_validation["status"]
        }

    # Stage 4: Content Extraction
    print("\n========== CONTENT EXTRACTION ==========")

    try:
        extraction = extract_content(
            file_path,
            detection["file_type"]
        )

    except Exception as error:
        return {
            "status": "extraction_failed",
            "file_name": detection["file_name"],
            "error": str(error)
        }

    return {
        "status": (
            "accepted"
            if extraction["status"] == "extracted"
            else "accepted_no_text_extraction"
        ),
        "file_name": detection["file_name"],
        "file_type": detection["file_type"],
        "mime_type": detection["mime_type"],
        "confidence": detection["confidence"],
        "extraction": extraction
    }

# ==========================================
# 5. Program Entry Point
# ==========================================
if __name__ == "__main__":

    file_path = input(
        "Enter file path: "
    ).strip().strip('"')

    try:
        result = process_file(file_path)

        print("\n========== FINAL RESULT ==========")
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str
            )
        )

    except Exception as error:
        print("\nERROR:", str(error))