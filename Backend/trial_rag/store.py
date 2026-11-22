"""SQLite store for the document trial. Separate from Core: no Postgres, tenants or RBAC."""
import io
import os
import re
import shutil
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("TRIAL_RAG_DB") or Path(__file__).resolve().parents[2] / ".local" / "trial_rag.db")
MAX_BYTES = 20 * 1024 * 1024
MIN_PAGE_CHARS = 20      # a PDF page with less extractable text is treated as scanned
MAX_OCR_PAGES = 30       # OCR is slow; refuse rather than silently truncating
OCR_LANGS = os.getenv("TRIAL_RAG_OCR_LANGS", "ara+eng")
MAX_TEXT_CHARS = 2_000_000
OCR_DPI = 200
OCR_PAGE_TIMEOUT = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, name TEXT NOT NULL, chars INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
    position INTEGER NOT NULL, text TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2');
"""


_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})


def fold(text):
    """Light Arabic folding so الإجازة / إجازة and سنوية / السنوية match in the index."""
    text = re.sub("[ً-ْـ]", "", text).translate(_FOLD).lower()
    return re.sub(r"\bال(?=\w{3})", "", text)


def open_db(path=None):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _tesseract():
    """Return a configured pytesseract module or raise a clear, bounded error."""
    try:
        import pytesseract
    except ImportError:
        raise ValueError("OCR needs: pip install pytesseract pillow pymupdf") from None
    command = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if not Path(command).exists():
        raise ValueError("OCR needs the Tesseract program with the 'ara' language data installed")
    pytesseract.pytesseract.tesseract_cmd = command
    return pytesseract


def _ocr(pytesseract, image):
    try:
        return pytesseract.image_to_string(image, lang=OCR_LANGS, timeout=OCR_PAGE_TIMEOUT).strip()
    except pytesseract.TesseractError:
        raise ValueError("OCR failed; check that Tesseract has the 'ara' and 'eng' language data") from None
    except RuntimeError:
        raise ValueError("OCR timed out on a page") from None


def ocr_pdf_pages(data, indexes):
    pytesseract = _tesseract()
    try:
        import pymupdf
        from PIL import Image
    except ImportError:
        raise ValueError("OCR needs: pip install pytesseract pillow pymupdf") from None
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for index in indexes:
            pixmap = document[index].get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
            image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
            yield index, _ocr(pytesseract, image)


def ocr_image(data):
    pytesseract = _tesseract()
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise ValueError("Cannot read image file") from None
    return _ocr(pytesseract, image)


def _check_size(lines):
    total = 0
    for line in lines:
        total += len(line) + 1
        if total > MAX_TEXT_CHARS:
            raise ValueError("Document text is too large for the trial")
        yield line


def read_docx(data):
    """Paragraphs and tables in document order; table rows become 'a | b | c'."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise ValueError("DOCX needs: pip install python-docx") from None
    try:
        document = Document(io.BytesIO(data))
    except Exception:
        raise ValueError("Cannot read docx file") from None

    def lines():
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                yield Paragraph(child, document).text.strip()
            elif child.tag.endswith("}tbl"):
                for row in Table(child, document).rows:
                    cells = []
                    for cell in row.cells:
                        value = cell.text.strip().replace("\n", " ")
                        if value and (not cells or cells[-1] != value):  # merged cells repeat
                            cells.append(value)
                    yield " | ".join(cells)

    return "\n".join(line for line in _check_size(lines()) if line)


def read_xlsx(data):
    """Each sheet row becomes 'header: value | ...' so chunks keep their column meaning."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ValueError("XLSX needs: pip install openpyxl") from None
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError("Cannot read xlsx file") from None

    def lines():
        for sheet in workbook.worksheets:
            yield f"[Sheet: {sheet.title}]"
            headers = None
            for row in sheet.iter_rows(values_only=True):
                values = ["" if v is None else str(v).strip() for v in row]
                if not any(values):
                    continue
                if headers is None:
                    headers = values
                    yield " | ".join(v for v in values if v)
                    continue
                yield " | ".join(f"{headers[i]}: {v}" if i < len(headers) and headers[i] else v
                                 for i, v in enumerate(values) if v)

    try:
        return "\n".join(_check_size(lines()))
    finally:
        workbook.close()


def read_pptx(data):
    """Per slide: text shapes (including groups), tables and speaker notes, labelled '[Slide n]'."""
    try:
        from pptx import Presentation
    except ImportError:
        raise ValueError("PPTX needs: pip install python-pptx") from None
    try:
        presentation = Presentation(io.BytesIO(data))
    except Exception:
        raise ValueError("Cannot read pptx file") from None

    def shape_lines(shapes):
        for shape in shapes:
            if shape.shape_type == 6:  # group
                yield from shape_lines(shape.shapes)
            elif getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                    yield " | ".join(c for c in cells if c)
            elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    yield "".join(run.text for run in paragraph.runs).strip()

    def lines():
        for number, slide in enumerate(presentation.slides, 1):
            yield f"[Slide {number}]"
            yield from shape_lines(slide.shapes)
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    yield "Notes: " + notes.replace("\n", " ")

    return "\n".join(line for line in _check_size(lines()) if line)


def extract_text(name, data):
    ext = Path(name).suffix.lower()
    if ext == ".pptx":
        text = read_pptx(data)
    elif ext == ".docx":
        text = read_docx(data)
    elif ext == ".xlsx":
        text = read_xlsx(data)
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF needs: pip install pypdf (scanned PDFs need OCR and are not supported)") from None
        pages = [(page.extract_text() or "").strip() for page in PdfReader(io.BytesIO(data)).pages]
        scanned = [i for i, page_text in enumerate(pages) if len(page_text) < MIN_PAGE_CHARS]
        if scanned:
            if len(scanned) > MAX_OCR_PAGES:
                raise ValueError(f"Scanned PDF has {len(scanned)} pages without text; OCR limit is {MAX_OCR_PAGES}")
            for index, ocr_text in ocr_pdf_pages(data, scanned):
                pages[index] = ocr_text
        text = "\n".join(pages)
    elif ext in {".png", ".jpg", ".jpeg"}:
        text = ocr_image(data)
    elif ext in {".txt", ".md", ".csv"}:
        for encoding in ("utf-8-sig", "cp1256"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("Cannot decode text file")
    else:
        raise ValueError("Supported types: .txt .md .csv .pdf .docx .xlsx .pptx .png .jpg")
    text = text.strip()
    if not text:
        raise ValueError("No extractable text found")
    return text


def chunk_text(text, size=800):
    """Greedy paragraph packing; over-long paragraphs are split on spaces."""
    chunks, current = [], ""
    for paragraph in re.split(r"\n+", text):
        paragraph = paragraph.strip()
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 1 > size:
            chunks.append(current)
            current = ""
        current = (current + "\n" + paragraph).strip()
    if current:
        chunks.append(current)
    return chunks


def add_document(conn, name, text):
    chunks = chunk_text(text)
    with conn:
        doc_id = conn.execute("INSERT INTO documents(name, chars) VALUES (?, ?)", (name, len(text))).lastrowid
        for position, chunk in enumerate(chunks, 1):
            chunk_id = conn.execute("INSERT INTO chunks(document_id, position, text) VALUES (?,?,?)",
                                    (doc_id, position, chunk)).lastrowid
            conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (chunk_id, fold(chunk)))
    return doc_id, len(chunks)


def search(conn, question, document_id=None, limit=5):
    tokens = [t for t in re.findall(r"\w+", fold(question)) if len(t) > 1]
    if not tokens:
        return []
    match = " OR ".join('"%s"%s' % (t, "*" if len(t) > 2 else "") for t in tokens)
    sql = ("SELECT c.text, d.id AS document_id, d.name, c.position FROM chunks_fts "
           "JOIN chunks c ON c.id = chunks_fts.rowid JOIN documents d ON d.id = c.document_id "
           "WHERE chunks_fts MATCH ?")
    args = [match]
    if document_id is not None:
        sql += " AND d.id = ?"
        args.append(document_id)
    sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
    args.append(limit)
    return [dict(row) for row in conn.execute(sql, args)]
