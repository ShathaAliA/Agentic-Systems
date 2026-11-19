"""P2-2: Mixed PDF page (text + image) → OCR appended, text preserved."""
import io
import uuid
from pathlib import Path

import pytest
import pymupdf
from PIL import Image, ImageDraw

from app.documents.file_processor import process_file


def _make_mixed_pdf(tmp_path: Path) -> Path:
    """Create a PDF with: 1 text-only page, 1 mixed page (text + image)."""
    # Create an image with Arabic-ish text (for OCR)
    img = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((50, 80), "MIXED PAGE OCR TEXT", fill="black")
    img_path = tmp_path / "ocr_source.png"
    img.save(img_path)

    pdf_path = tmp_path / "mixed.pdf"
    doc = pymupdf.open()

    # Page 1: text only
    page1 = doc.new_page()
    page1.insert_text((50, 100), "TEXT ONLY PAGE CONTENT", fontsize=14)

    # Page 2: text + image
    page2 = doc.new_page()
    page2.insert_text((50, 50), "MIXED PAGE TEXT PART", fontsize=14)
    page2.insert_image(pymupdf.Rect(50, 100, 550, 300), filename=str(img_path))

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_mixed_page_ocr_appended(tmp_path):
    pdf_path = _make_mixed_pdf(tmp_path)

    r = process_file(str(pdf_path))
    assert r["status"] == "accepted", f"Unexpected status: {r.get('reason')}"

    extraction = r["extraction"]
    pages = extraction.get("pages", [])
    assert len(pages) == 2, f"Expected 2 pages, got {len(pages)}"

    # Page 1: text only (no OCR tag)
    page1_text = pages[0]["text"]
    assert "TEXT ONLY PAGE CONTENT" in page1_text
    assert "[OCR]" not in page1_text, "Text-only page should not have OCR"

    # Page 2: mixed (text + image → OCR appended)
    page2_text = pages[1]["text"]
    assert "MIXED PAGE TEXT PART" in page2_text, (
        f"Original text lost: {page2_text[:200]}"
    )
    assert "[OCR]" in page2_text, (
        f"OCR not appended for mixed page: {page2_text[:200]}"
    )
    # OCR content should include the image text
    assert "MIXED PAGE OCR TEXT" in page2_text or "OCR TEXT" in page2_text, (
        f"OCR text not found: {page2_text[:300]}"
    )


def test_scanned_page_ocr_only(tmp_path):
    """Scanned page (no text, only image) → OCR only, no [OCR] tag."""
    img = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((50, 80), "SCANNED PAGE TEXT", fill="black")
    img_path = tmp_path / "scanned.png"
    img.save(img_path)

    pdf_path = tmp_path / "scanned.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 550, 250), filename=str(img_path))
    doc.save(str(pdf_path))
    doc.close()

    r = process_file(str(pdf_path))
    assert r["status"] == "accepted"

    text = r["extraction"]["original_text"]
    assert "SCANNED PAGE TEXT" in text or "SCANNED" in text, (
        f"OCR text not found: {text[:200]}"
    )
    assert "[OCR]" not in text, "Scanned-only page should not have [OCR] tag"