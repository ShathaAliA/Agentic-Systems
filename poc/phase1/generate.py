"""Generate seven authored Arabic artifacts; no OCR, extraction, embedding or LLM."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import argparse
import pymupdf
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from dataset import ROOT, manifest, write_json, file_hash


def pdf(row, target):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    title = escape(row["name"])
    text = "<br>".join(escape(line) for line in row["expected_text"].splitlines())
    html = f'<div dir="rtl" style="font-size:24px"><h2>{title}</h2><p>{text}</p><p>بيانات اصطناعية للاختبار فقط</p></div>'
    spare, scale = page.insert_htmlbox(pymupdf.Rect(35, 35, 560, 800), html)
    assert spare >= 0 and scale == 1, "Synthetic content must fit without scaling"
    if row.get("scanned"):
        image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
        doc.close()
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image)  # Image-only PDF, no hidden text.
    doc.set_metadata({"title": row["name"], "author": "Synthetic PoC Generator"})
    doc.save(target, no_new_id=True, garbage=4, deflate=True)
    doc.close()


def generate(destination=ROOT):
    destination = Path(destination)
    folder = destination / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    data = manifest()
    for row in data["documents"]:
        path = destination / row["file"]
        if path.suffix == ".pdf":
            pdf(row, path)
        elif path.suffix == ".docx":
            document = Document()
            document.add_paragraph(row["name"])
            document.add_paragraph(row["expected_text"])
            for paragraph in document.paragraphs:
                element = OxmlElement("w:bidi")
                paragraph._p.get_or_add_pPr().append(element)
                for run in paragraph.runs:
                    run.font.name = "Arial"
            document.core_properties.created = datetime(2000, 1, 1, tzinfo=timezone.utc)
            document.core_properties.modified = document.core_properties.created
            document.save(path)
        elif path.suffix == ".xlsx":
            book = Workbook()
            sheet = book.active
            sheet.title = "البدلات"
            sheet.sheet_view.rightToLeft = True
            for values in [("المدينة", "البدل التجريبي", "اعتماد مسبق"), ("مدينة س", 150, "نعم"), ("مدينة ص", 200, "نعم")]:
                sheet.append(values)
            sheet.column_dimensions["A"].width = 25
            sheet.column_dimensions["B"].width = 25
            sheet.column_dimensions["C"].width = 25
            book.properties.created = datetime(2000, 1, 1)
            book.properties.modified = book.properties.created
            book.save(path)
            book.close()
        else:
            path.write_text(f'# {row["name"]}\n\n{row["expected_text"]}\n', encoding="utf-8")
        row["sha256"] = file_hash(path)
        row["byte_size"] = path.stat().st_size
    write_json(destination / "ground-truth.json", data)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    generate()
    print("Generated seven synthetic files and exact ground-truth.json; no processing or model calls")
