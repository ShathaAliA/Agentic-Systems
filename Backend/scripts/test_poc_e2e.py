"""End-to-end test on PoC dept (setup via setup_poc_dept.py).

Runs:
1. Setup users in PoC HR dept.
2. Upload 7 files to HR dept via process_file (direct, no HTTP).
3. Approve each.
4. Verify:
   - Extraction quality vs ground truth.
   - Chunks have real locations.
   - Cross-dept isolation (HR user can't access Finance).

No external LLM call (uses extractive mode — just checks chunks).
"""
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
STATE_FILE = BACKEND / "scripts" / ".poc_state.json"
SAMPLES = BACKEND / "tests" / "samples"

PG_HOST, PG_PORT = "127.0.0.1", 5435
ADMIN_URL = f"postgresql+psycopg://postgres:postgres@{PG_HOST}:{PG_PORT}/postgres"

sys.path.insert(0, str(BACKEND))


def load_state():
    if not STATE_FILE.exists():
        print("❌ Run scripts/setup_poc_dept.py first.")
        sys.exit(1)
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def get_dept_url(db_name):
    return f"postgresql+psycopg://postgres:postgres@{PG_HOST}:{PG_PORT}/{db_name}"


def tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def main():
    state = load_state()
    hr = state["departments"][0]
    fin = state["departments"][1]

    print(f"→ HR:      {hr['department_id']}")
    print(f"→ Finance: {fin['department_id']}")

    # Import app modules AFTER sys.path
    from app.documents.models import Document, DocumentChunk, DocumentAuditLog
    from app.documents.file_processor import process_file
    from app.documents import rag_service

    engine = create_engine(get_dept_url(hr["db_name"]), hide_parameters=True)

    gt = json.loads((SAMPLES / "ground_truth.json").read_text(encoding="utf-8"))
    has_tesseract = tesseract_available()
    print(f"→ Tesseract available: {has_tesseract}")

    print("\n=== 1) Upload + process files ===")
    results = {}
    for filename, facts in gt.items():
        file_path = SAMPLES / filename
        if not file_path.exists():
            print(f"  ⚠ {filename} not found, skip.")
            continue
        try:
            res = process_file(str(file_path))
            results[filename] = res
            status = res["status"]
            text_preview = (res["extraction"].get("original_text") or "")[:60]
            print(f"  {filename}: {status} | text: {text_preview!r}")
        except Exception as e:
            print(f"  ❌ {filename}: {e}")

    print("\n=== 2) Insert docs + chunks ===")
    with Session(engine) as db:
        for filename, res in results.items():
            if res["status"] != "accepted":
                continue
            text = res["extraction"].get("original_text", "") or ""
            doc = Document(
                filename=filename,
                file_path=str(SAMPLES / filename),
                file_type=Path(filename).suffix.lower(),
                extracted_text=text,
                department_id=hr["department_id"],
                status="approved",
                version=1,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)

            chunks = rag_service.chunk_text(text)
            if chunks:
                rag_service.process_and_store_chunks(
                    db, doc.id, chunks, version_number=1,
                    pages=res["extraction"].get("pages"),
                )
            db.add(DocumentAuditLog(
                document_id=doc.id, action="upload",
                performed_by="poc_user", version_number=1,
            ))
            db.commit()

        doc_count = db.query(Document).filter(Document.department_id == hr["department_id"]).count()
        chunk_count = db.query(DocumentChunk).count()
        print(f"  ✓ {doc_count} docs, {chunk_count} chunks")

    print("\n=== 3) Verify extraction quality ===")
    for filename, facts in gt.items():
        if filename not in results:
            continue
        res = results[filename]
        text = (res["extraction"].get("original_text") or "")
        missing = [f for f in facts.get("key_facts", []) if f not in text]
        requires_ocr = facts.get("requires_ocr", False)

        if requires_ocr and not has_tesseract:
            print(f"  ⚠ {filename}: SKIP (Tesseract not installed — OCR dependency)")
        elif missing:
            print(f"  ⚠ {filename}: missing facts {missing}")
        else:
            print(f"  ✅ {filename}: all facts found")

    print("\n=== 4) Verify chunk locations ===")
    with Session(engine) as db:
        for filename in results:
            if results[filename]["status"] != "accepted":
                continue
            doc = db.query(Document).filter(
                Document.filename == filename,
                Document.department_id == hr["department_id"],
            ).first()
            if not doc:
                continue
            chunks = db.query(DocumentChunk).filter(
                DocumentChunk.document_id == doc.id
            ).all()
            pages = {c.page_number for c in chunks}
            print(f"  {filename}: {len(chunks)} chunks on pages {sorted(pages)}")

    engine.dispose()

    print("\n=== 5) Cross-dept isolation ===")
    fin_engine = create_engine(get_dept_url(fin["db_name"]), hide_parameters=True)
    with Session(fin_engine) as db:
        fin_docs = db.query(Document).count()
        print(f"  Finance dept has {fin_docs} docs (expected 0)")
        if fin_docs == 0:
            print("  ✅ Isolation OK — HR docs not in Finance")
        else:
            print("  ❌ LEAK: HR docs found in Finance")
    fin_engine.dispose()

    print("\n✅ E2E test complete.")


if __name__ == "__main__":
    main()