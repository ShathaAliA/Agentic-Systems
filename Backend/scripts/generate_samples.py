"""Generate 7 synthetic test files + ground truth JSON.

Creates:
- hr_policy.pdf       (digital PDF, English + numbers)
- hr_scanned.pdf      (scanned PDF, requires Tesseract for OCR)
- finance.xlsx        (Excel with tables)
- legal_notes.docx    (Word, Arabic-friendly)
- faq.md              (Markdown)
- data.csv            (CSV)
- org_chart.png       (image, requires Tesseract for OCR)

Output: tests/samples/ + ground_truth.json

NOTE: pymupdf.insert_text does not render Arabic glyphs without an
embedded Arabic font. Arabic text is tested via DOCX/CSV/MD (UTF-8).
PDF here uses English + numbers.
"""
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw
from docx import Document
from openpyxl import Workbook

OUT = Path(__file__).resolve().parents[1] / "tests" / "samples"
OUT.mkdir(parents=True, exist_ok=True)

GROUND_TRUTH = {}


def gen_hr_policy_pdf():
    """Digital PDF with text (English + numbers)."""
    path = OUT / "hr_policy.pdf"
    doc = pymupdf.open()
    pages = [
        "HR Policy - Leave\n\n"
        "Employee is entitled to 30 days annual leave.\n"
        "Sick leave: 15 days full pay, then 30 days half pay.\n",
        "HR Policy - Salary\n\n"
        "Base salary: 15000 SAR.\n"
        "Housing allowance: 25% of base salary.\n"
        "Transport: 1000 SAR monthly.\n",
        "HR Policy - Remote Work\n\n"
        "Remote work: 2 days per week with manager approval.\n",
    ]
    for page_text in pages:
        page = doc.new_page()
        page.insert_text((50, 100), page_text, fontsize=12)
    doc.save(str(path))
    doc.close()
    GROUND_TRUTH["hr_policy.pdf"] = {
        "pages": 3,
        "key_facts": [
            "30 days annual leave",
            "15000 SAR",
            "Housing allowance: 25%",
            "2 days per week",
        ],
    }
    print(f"  ✓ {path.name}")


def gen_hr_scanned_pdf():
    """Scanned PDF (image only) — requires Tesseract for OCR."""
    img = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(img)
    draw.text((50, 100), "HR Leave Policy (Scanned)", fill="black")
    draw.text((50, 150), "Annual leave: 30 days", fill="black")
    draw.text((50, 200), "Sick leave: 15 days full pay", fill="black")
    temp_img = OUT / "_temp_scanned.png"
    img.save(str(temp_img))

    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_image(pymupdf.Rect(0, 0, 600, 400), filename=str(temp_img))
    doc.save(str(OUT / "hr_scanned.pdf"))
    doc.close()
    temp_img.unlink()
    GROUND_TRUTH["hr_scanned.pdf"] = {
        "pages": 1,
        "key_facts": ["Annual leave: 30 days", "Sick leave: 15 days"],
        "requires_ocr": True,
    }
    print(f"  ✓ hr_scanned.pdf (requires Tesseract for OCR)")


def gen_finance_xlsx():
    path = OUT / "finance.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Department", "Budget 2026", "Spent", "Remaining"])
    ws.append(["HR", 500000, 120000, 380000])
    ws.append(["Finance", 800000, 350000, 450000])
    ws.append(["IT", 1200000, 400000, 800000])
    ws2 = wb.create_sheet("Summary")
    ws2.append(["Total Budget", 2500000])
    wb.save(str(path))
    GROUND_TRUTH["finance.xlsx"] = {
        "sheets": 2,
        "key_facts": ["500000", "800000", "1200000", "2500000"],
    }
    print(f"  ✓ finance.xlsx")


def gen_legal_notes_docx():
    path = OUT / "legal_notes.docx"
    doc = Document()
    doc.add_heading("Legal Notes", level=1)
    doc.add_paragraph("Contract review: need legal approval before signing.")
    doc.add_paragraph("NDA templates: available in shared drive.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Client"
    table.cell(0, 1).text = "Contract Value"
    table.cell(1, 0).text = "Acme Corp"
    table.cell(1, 1).text = "500000 SAR"
    doc.save(str(path))
    GROUND_TRUTH["legal_notes.docx"] = {
        "key_facts": ["Acme Corp", "500000 SAR", "NDA templates"],
    }
    print(f"  ✓ legal_notes.docx")


def gen_faq_md():
    path = OUT / "faq.md"
    path.write_text(
        "# FAQ\n\n"
        "## What is the refund policy?\n"
        "Refunds within 30 days of purchase.\n\n"
        "## How do I request a refund?\n"
        "Contact support@example.com with your order ID.\n",
        encoding="utf-8",
    )
    GROUND_TRUTH["faq.md"] = {"key_facts": ["30 days", "support@example.com"]}
    print(f"  ✓ faq.md")


def gen_data_csv():
    path = OUT / "data.csv"
    path.write_text(
        "employee_id,name,salary,department\n"
        "E001,Ahmed,15000,HR\n"
        "E002,Fatima,18000,Finance\n"
        "E003,Omar,12000,HR\n",
        encoding="utf-8",
    )
    GROUND_TRUTH["data.csv"] = {"key_facts": ["E001", "15000", "Fatima", "18000"]}
    print(f"  ✓ data.csv")


def gen_org_chart_png():
    path = OUT / "org_chart.png"
    img = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(img)
    draw.text((300, 50), "CEO", fill="black")
    draw.text((150, 200), "CTO", fill="black")
    draw.text((450, 200), "CFO", fill="black")
    img.save(str(path))
    GROUND_TRUTH["org_chart.png"] = {
        "key_facts": ["CEO", "CTO", "CFO"],
        "requires_ocr": True,
    }
    print(f"  ✓ org_chart.png (requires Tesseract for OCR)")


def main():
    print("Generating test samples...")
    gen_hr_policy_pdf()
    gen_hr_scanned_pdf()
    gen_finance_xlsx()
    gen_legal_notes_docx()
    gen_faq_md()
    gen_data_csv()
    gen_org_chart_png()

    gt_path = OUT / "ground_truth.json"
    gt_path.write_text(
        json.dumps(GROUND_TRUTH, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ 7 files + ground truth saved to: {OUT}")


if __name__ == "__main__":
    main()