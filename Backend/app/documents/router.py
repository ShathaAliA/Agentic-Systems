from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import shutil
import os
from app.database import get_db
from app.documents.models import Document
from app.documents.ocr_service import extract_text_from_file
from app.documents import rag_service

router = APIRouter(prefix="/api/documents", tags=["Documents"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    allowed_extensions = [".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="نوع الملف غير مدعوم")
    
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    extracted_text = extract_text_from_file(file_path, file_ext)
        
    new_doc = Document(
        filename=file.filename,
        file_path=file_path,
        file_type=file_ext,
        extracted_text=extracted_text
    )
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)
    
    chunks = rag_service.chunk_text(extracted_text)
    if chunks:
        rag_service.process_and_store_chunks(db, new_doc.id, chunks)
    
    return {
        "status": "success",
        "document_id": new_doc.id,
        "chunks_count": len(chunks),
        "message": "تم الرفع، الاستخراج، التقسيم، والتضمين بنجاح."
    }

@router.post("/ask")
async def ask_question(query: str, db: Session = Depends(get_db)):
    context_chunks = rag_service.retrieve_relevant_chunks(db, query)
    if not context_chunks:
        return {"answer": "لم يتم العثور على معلومات ذات صلة في قاعدة البيانات."}
    
    context = "\n\n".join(context_chunks)
    answer = rag_service.generate_answer(query, context)
    
    return {
        "query": query,
        "answer": answer,
        "context_used": context_chunks
    }

@router.get("/")
async def list_documents(db: Session = Depends(get_db)):
    documents = db.query(Document).all()
    return {"count": len(documents), "documents": documents}

@router.get("/download/{document_id}")
async def download_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    return FileResponse(path=doc.file_path, filename=doc.filename)

@router.delete("/{document_id}")
async def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    db.delete(doc)
    db.commit()
    return {"status": "success", "message": "تم الحذف بنجاح"}