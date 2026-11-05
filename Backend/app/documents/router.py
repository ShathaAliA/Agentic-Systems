from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import shutil
import os
import uuid

from app.database import get_db
from app.documents.models import Document, DocumentAuditLog # <-- ضفنا AuditLog
from app.documents.ocr_service import extract_text_from_file
from app.documents import rag_service
from app.auth.dependencies import get_current_user
from app.models import User
from app.services.authorization import has_permission

router = APIRouter(prefix="/api/documents", tags=["Documents"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    department_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not has_permission(db, current_user.id, "documents:upload", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    MAX_SIZE = 20 * 1024 * 1024
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > MAX_SIZE:
        raise HTTPException(status_code=400, detail="حجم الملف يتجاوز 20 ميجابايت")

    allowed_extensions = [".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="نوع الملف غير مدعوم")
    
    existing_doc = db.query(Document).filter(
        Document.filename == file.filename,
        Document.department_id == department_id
    ).first()
    
    if existing_doc:
        existing_doc.status = "archived"
        # تدقيق الأرشفة
        audit_archive = DocumentAuditLog(document_id=existing_doc.id, action="archive", performed_by=current_user.id)
        db.add(audit_archive)
        db.commit()
    
    safe_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    extracted_text = extract_text_from_file(file_path, file_ext)
        
    new_doc = Document(
        filename=file.filename,
        file_path=file_path,
        file_type=file_ext,
        extracted_text=extracted_text,
        department_id=department_id,
        status="pending_review",
        version=existing_doc.version + 1 if existing_doc else 1
    )
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)
    
    # تدقيق الرفع
    audit_upload = DocumentAuditLog(document_id=new_doc.id, action="upload", performed_by=current_user.id)
    db.add(audit_upload)
    db.commit()
    
    chunks = rag_service.chunk_text(extracted_text)
    if chunks:
        rag_service.process_and_store_chunks(db, new_doc.id, chunks)
    
    return {
        "status": "success",
        "document_id": new_doc.id,
        "chunks_count": len(chunks),
        "doc_version": new_doc.version,
        "doc_status": new_doc.status,
        "message": "تم الرفع (بانتظار الموافقة)."
    }

@router.post("/{document_id}/approve")
async def approve_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    if not has_permission(db, current_user.id, "documents:update", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    doc.status = "approved"
    # تدقيق الموافقة
    audit_approve = DocumentAuditLog(document_id=doc.id, action="approve", performed_by=current_user.id)
    db.add(audit_approve)
    db.commit()
    
    return {"status": "success", "message": "تم اعتماد المستند ونشره في البحث."}

@router.post("/ask")
async def ask_question(
    query: str,
    department_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not has_permission(db, current_user.id, "documents:read", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    context_chunks = rag_service.retrieve_relevant_chunks(db, query, department_id)
    
    if not context_chunks:
        return {"answer": "لم يتم العثور على معلومات ذات صلة في قاعدة البيانات."}
    
    context = "\n\n".join([c["content"] for c in context_chunks])
    answer = rag_service.generate_answer(query, context)
    
    return {
        "query": query,
        "answer": answer,
        "sources": [
            {"document": c["document_name"], "chunk_id": c["chunk_id"]}
            for c in context_chunks
        ]
    }

@router.get("/")
async def list_documents(
    department_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not has_permission(db, current_user.id, "documents:read", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    documents = db.query(Document).filter(Document.department_id == department_id).all()
    return {"count": len(documents), "documents": documents}

@router.get("/download/{document_id}")
async def download_document(
    document_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    if not has_permission(db, current_user.id, "documents:read", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    return FileResponse(path=doc.file_path, filename=doc.filename)

@router.delete("/{document_id}")
async def delete_document(
    document_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    if not has_permission(db, current_user.id, "documents:delete", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    doc.status = "archived"
    # تدقيق الأرشفة
    audit_archive = DocumentAuditLog(document_id=doc.id, action="archive", performed_by=current_user.id)
    db.add(audit_archive)
    db.commit()
    
    return {"status": "success", "message": "تم أرشفة المستند."}