import pytest
from fastapi.testclient import TestClient

import app.llm.client as llm_client
from trial_rag import main, store

TEXT = "سياسة الإجازات\n\nيحق لكل موظف إجازة سنوية مدتها 30 يوما في السنة.\n\nساعات الدوام من 8 إلى 4."


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_API_KEY", "k")
    return TestClient(main.app)


def test_upload_ask_answer(client, monkeypatch):
    seen = {}

    def fake_chat(self, messages, **options):
        seen["messages"] = messages
        return "30 يوما [1]"

    monkeypatch.setattr(llm_client.ChatClient, "chat", fake_chat)
    uploaded = client.post("/documents", files={"file": ("hr.txt", TEXT.encode("utf-8"))})
    assert uploaded.status_code == 200 and uploaded.json()["chunks"] >= 1
    answer = client.post("/ask", json={"question": "كم مدة الإجازة السنوية؟"}).json()
    assert answer["status"] == "answered" and answer["sources"][0]["document"] == "hr.txt"
    assert "30 يوما" in seen["messages"][1]["content"]


def test_no_match_skips_model_and_bad_type_rejected(client, monkeypatch):
    monkeypatch.setattr(llm_client.ChatClient, "chat", lambda *a, **k: pytest.fail("model called"))
    client.post("/documents", files={"file": ("hr.txt", TEXT.encode("utf-8"))})
    assert client.post("/ask", json={"question": "zzzz qqqq"}).json()["status"] == "no_answer"
    assert client.post("/documents", files={"file": ("x.exe", b"abc")}).status_code == 422


def test_scanned_pdf_pages_fall_back_to_ocr(monkeypatch):
    import pymupdf

    document = pymupdf.open()
    document.new_page()
    data = document.tobytes()
    monkeypatch.setattr(store, "ocr_pdf_pages", lambda payload, indexes: iter([(i, "نص ممسوح ضوئيا") for i in indexes]))
    assert store.extract_text("scan.pdf", data) == "نص ممسوح ضوئيا"


def test_docx_paragraphs_and_tables_in_order():
    import io
    from docx import Document

    document = Document()
    document.add_paragraph("سياسة الإجازات")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "الفئة", "الأيام"
    table.cell(1, 0).text, table.cell(1, 1).text = "خمس سنوات", "30"
    document.add_paragraph("نهاية")
    buffer = io.BytesIO()
    document.save(buffer)
    assert store.extract_text("p.docx", buffer.getvalue()).splitlines() == [
        "سياسة الإجازات", "الفئة | الأيام", "خمس سنوات | 30", "نهاية"]


def test_xlsx_rows_keep_their_headers():
    import io
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Leave"
    sheet.append(["الفئة", "الأيام"])
    sheet.append(["خمس سنوات", 30])
    buffer = io.BytesIO()
    workbook.save(buffer)
    text = store.extract_text("l.xlsx", buffer.getvalue())
    assert "[Sheet: Leave]" in text and "الفئة: خمس سنوات | الأيام: 30" in text


def test_corrupt_office_files_are_rejected():
    for name in ("x.docx", "x.xlsx"):
        with pytest.raises(ValueError):
            store.extract_text(name, b"not a zip")


def test_pptx_slides_tables_and_notes():
    import io
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "سياسة الإجازات"
    slide.placeholders[1].text = "21 يوما لأقل من خمس سنوات"
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(0, 1).text = "الفئة", "الأيام"
    table.cell(1, 0).text, table.cell(1, 1).text = "خمس سنوات", "30"
    slide.notes_slide.notes_text_frame.text = "ملاحظة المتحدث"
    buffer = io.BytesIO()
    presentation.save(buffer)
    text = store.extract_text("p.pptx", buffer.getvalue())
    assert text.splitlines()[0] == "[Slide 1]"
    for expected in ("سياسة الإجازات", "21 يوما", "خمس سنوات | 30", "Notes: ملاحظة المتحدث"):
        assert expected in text


def test_corrupt_pptx_rejected():
    with pytest.raises(ValueError):
        store.extract_text("x.pptx", b"not a zip")
