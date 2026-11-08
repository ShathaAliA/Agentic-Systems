from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import shutil
import os
import uuid

from app.database import get_department_db
from app.documents.models import Document, DocumentAuditLog, DocumentVersion, DocumentRevisionLog, DocumentChunk
from app.documents.file_processor import process_file
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
    db: Session = Depends(get_department_db),
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

    file_ext = os.path.splitext(file.filename)[1].lower()
    temp_filename = f"temp_{uuid.uuid4()}{file_ext}"
    temp_path = os.path.join(UPLOAD_DIR, temp_filename)
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        result = process_file(temp_path)
    except Exception as e:
        os.remove(temp_path)
        raise HTTPException(status_code=400, detail=f"File processing error: {str(e)}")

    if result["status"] != "accepted":
        os.remove(temp_path)
        reason = result.get("reason", "unknown")
        raise HTTPException(status_code=400, detail=f"File rejected: {reason}")

    safe_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    shutil.move(temp_path, file_path)

    existing_doc = db.query(Document).filter(
        Document.filename == file.filename,
        Document.department_id == department_id
    ).first()
    
    if existing_doc:
        existing_doc.status = "archived"
        audit_archive = DocumentAuditLog(document_id=existing_doc.id, action="archive", performed_by=current_user.id)
        db.add(audit_archive)
        
        existing_version = db.query(DocumentVersion).filter(
            DocumentVersion.document_id == existing_doc.id,
            DocumentVersion.status == "approved"
        ).first()
        if existing_version:
            existing_version.status = "archived"
        
        db.commit()
    
    extracted_text = result["extraction"].get("original_text", "")
        
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
    
    new_version = DocumentVersion(
        document_id=new_doc.id,
        version_number=new_doc.version,
        status="pending"
    )
    db.add(new_version)
    db.commit()
    
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
    department_id: str,
    db: Session = Depends(get_department_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    if not has_permission(db, current_user.id, "documents:update", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    doc.status = "approved"
    
    version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == doc.id,
        DocumentVersion.version_number == doc.version
    ).first()
    if version:
        version.status = "approved"
    
    audit_approve = DocumentAuditLog(document_id=doc.id, action="approve", performed_by=current_user.id)
    db.add(audit_approve)
    db.commit()
    
    return {"status": "success", "message": "تم اعتماد المستند ونشره في البحث."}

@router.post("/{document_id}/revise")
async def revise_document(
    document_id: int,
    new_text: str,
    department_id: str,
    db: Session = Depends(get_department_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    if not has_permission(db, current_user.id, "documents:update", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    revision = DocumentRevisionLog(
        document_id=doc.id,
        old_text=doc.extracted_text,
        new_text=new_text,
        revised_by=current_user.id
    )
    db.add(revision)
    
    doc.extracted_text = new_text
    
    # إعادة معالجة المقاطع بعد التصحيح (زي ما شذى طلبت)
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
    chunks = rag_service.chunk_text(new_text)
    if chunks:
        rag_service.process_and_store_chunks(db, doc.id, chunks)
    
    db.commit()
    
    return {"status": "success", "message": "تم تصحيح النص وإعادة معالجة المقاطع."}

@router.post("/ask")
async def ask_question(
    query: str,
    department_id: str,
    db: Session = Depends(get_department_db),
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
            {"document": c["document_name"], "chunk_id": c["chunk_id"], "version": c.get("version")}
            for c in context_chunks
        ]
    }

@router.get("/")
async def list_documents(
    department_id: str,
    db: Session = Depends(get_department_db),
    current_user: User = Depends(get_current_user)
):
    if not has_permission(db, current_user.id, "documents:read", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    documents = db.query(Document).filter(Document.department_id == department_id).all()
    return {"count": len(documents), "documents": documents}

@router.get("/download/{document_id}")
async def download_document(
    document_id: int, 
    department_id: str,
    db: Session = Depends(get_department_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    # شذى طلبت: تنزيل الملف الأصلي لرئيس القسم أو Admin فقط
    if not has_permission(db, current_user.id, "documents:update", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied. Only department head or admin can download.")

    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    return FileResponse(path=doc.file_path, filename=doc.filename)

@router.delete("/{document_id}")
async def delete_document(
    document_id: int, 
    department_id: str,
    db: Session = Depends(get_department_db),
    current_user: User = Depends(get_current_user)
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="المستند غير موجود")
    
    if not has_permission(db, current_user.id, "documents:delete", doc.department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    doc.status = "archived"
    
    version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == doc.id,
        DocumentVersion.version_number == doc.version
    ).first()
    if version:
        version.status = "archived"
    
    audit_archive = DocumentAuditLog(document_id=doc.id, action="archive", performed_by=current_user.id)
    db.add(audit_archive)
    db.commit()
    
    return {"status": "success", "message": "تم أرشفة المستند."}