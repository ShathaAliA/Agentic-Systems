from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import shutil
import os
import uuid
import logging

from app.database import get_central_db
from app.database_router import authorized_department_session
from app.documents.models import (
    Document,
    DocumentAuditLog,
    DocumentVersion,
    DocumentRevisionLog,
    DocumentChunk,
)
from app.documents.audience_service import (
    get_user_context,
    filter_visible_documents,
    user_can_access_document,
)
from app.documents.file_processor import process_file
from app.documents import rag_service
from app.auth.dependencies import get_current_user
from app.models import User
from app.services.authorization import has_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["Documents"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ------------------------------------------------------------------
# Helper: فتح جلسة القسم بعد التحقق المركزي، مع رسائل خطأ آمنة.
# ------------------------------------------------------------------
def _open_department_session(user_id: str, department_id: str, permission_code: str):
    try:
        return authorized_department_session(
            user_id=user_id,
            department_id=department_id,
            permission_code=permission_code,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except ValueError as e:
        logger.warning("Department session rejected: %s", e)
        raise HTTPException(status_code=403, detail="Department not available")
    except RuntimeError as e:
        logger.exception("Department engine failure: %s", e)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


# ------------------------------------------------------------------
# Upload
# ------------------------------------------------------------------
@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    department_id: str = Form(...),
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:upload", department_id):
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
        logger.exception("Upload Error while processing file %s: %s", temp_path, e)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(
            status_code=400,
            detail="فشل معالجة الملف. تأكد من أن الملف سليم وغير تالف.",
        )

    if result["status"] != "accepted":
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reason = result.get("reason", "unknown")
        logger.warning("File rejected: %s (file=%s)", reason, file.filename)
        raise HTTPException(
            status_code=400,
            detail="الملف غير مدعوم أو تالف. يرجى التأكد من نوع الملف.",
        )

    safe_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    shutil.move(temp_path, file_path)

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:upload",
        ) as db:
            existing_doc = db.query(Document).filter(
                Document.filename == file.filename,
                Document.department_id == department_id,
            ).first()

            if existing_doc:
                existing_doc.status = "archived"
                db.add(DocumentAuditLog(
                    document_id=existing_doc.id,
                    action="archive",
                    performed_by=current_user.id,
                ))
                existing_version = db.query(DocumentVersion).filter(
                    DocumentVersion.document_id == existing_doc.id,
                    DocumentVersion.status == "approved",
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
                version=existing_doc.version + 1 if existing_doc else 1,
            )
            db.add(new_doc)
            db.commit()
            db.refresh(new_doc)

            db.add(DocumentVersion(
                document_id=new_doc.id,
                version_number=new_doc.version,
                status="pending",
            ))
            db.add(DocumentAuditLog(
                document_id=new_doc.id,
                action="upload",
                performed_by=current_user.id,
            ))
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
                "message": "تم الرفع (بانتظار الموافقة).",
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload DB error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Approve
# ------------------------------------------------------------------
@router.post("/{document_id}/approve")
async def approve_document(
    document_id: int,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:update", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:update",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            doc.status = "approved"
            version = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number == doc.version,
            ).first()
            if version:
                version.status = "approved"

            db.add(DocumentAuditLog(
                document_id=doc.id,
                action="approve",
                performed_by=current_user.id,
            ))
            db.commit()
            return {"status": "success", "message": "تم اعتماد المستند ونشره في البحث."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Approve error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Revise
# ------------------------------------------------------------------
@router.post("/{document_id}/revise")
async def revise_document(
    document_id: int,
    new_text: str,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:update", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:update",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            db.add(DocumentRevisionLog(
                document_id=doc.id,
                old_text=doc.extracted_text,
                new_text=new_text,
                revised_by=current_user.id,
            ))
            doc.extracted_text = new_text

            db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
            chunks = rag_service.chunk_text(new_text)
            if chunks:
                rag_service.process_and_store_chunks(db, doc.id, chunks)

            db.commit()
            return {"status": "success", "message": "تم تصحيح النص وإعادة معالجة المقاطع."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Revise error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Ask (with audience filtering)
# ------------------------------------------------------------------
@router.post("/ask")
async def ask_question(
    query: str,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:read", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    user_depts, user_roles = get_user_context(central_db, current_user.id)

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:read",
        ) as db:
            context_chunks = rag_service.retrieve_relevant_chunks(db, query, department_id)
            if not context_chunks:
                return {"answer": "لم يتم العثور على معلومات ذات صلة في قاعدة البيانات."}

            allowed_chunks = []
            for c in context_chunks:
                doc_id = c.get("document_id")
                if doc_id is None or user_can_access_document(db, doc_id, user_depts, user_roles):
                    allowed_chunks.append(c)

            if not allowed_chunks:
                return {"answer": "لم يتم العثور على معلومات مسموح لك بالوصول إليها."}

            context = "\n\n".join([c["content"] for c in allowed_chunks])
            try:
                answer = rag_service.generate_answer(query, context)
            except Exception as e:
                logger.exception("Provider error: %s", e)
                raise HTTPException(
                    status_code=502,
                    detail="تعذر الحصول على إجابة من المزود. يرجى المحاولة لاحقاً.",
                )

            return {
                "query": query,
                "answer": answer,
                "sources": [
                    {
                        "document": c["document_name"],
                        "chunk_id": c["chunk_id"],
                        "version": c.get("version"),
                    }
                    for c in allowed_chunks
                ],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ask error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# List (with audience filtering)
# ------------------------------------------------------------------
@router.get("/")
async def list_documents(
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:read", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    user_depts, user_roles = get_user_context(central_db, current_user.id)

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:read",
        ) as db:
            documents = db.query(Document).filter(
                Document.department_id == department_id,
            ).all()

            documents = filter_visible_documents(db, documents, user_depts, user_roles)

            safe_documents = [
                {
                    "id": d.id,
                    "filename": d.filename,
                    "file_type": d.file_type,
                    "status": d.status,
                    "version": d.version,
                    "upload_date": d.upload_date,
                }
                for d in documents
            ]
            return {"count": len(safe_documents), "documents": safe_documents}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("List error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Download (head/deputy/admin only)
# ------------------------------------------------------------------
@router.get("/download/{document_id}")
async def download_document(
    document_id: int,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:update", department_id):
        raise HTTPException(
            status_code=403,
            detail="Permission denied. Only department head or admin can download.",
        )

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:update",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")
            if not os.path.exists(doc.file_path):
                raise HTTPException(status_code=404, detail="الملف غير موجود")
            return FileResponse(path=doc.file_path, filename=doc.filename)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Download error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Delete → archive
# ------------------------------------------------------------------
@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:delete", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:delete",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            doc.status = "archived"
            version = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version_number == doc.version,
            ).first()
            if version:
                version.status = "archived"

            db.add(DocumentAuditLog(
                document_id=doc.id,
                action="archive",
                performed_by=current_user.id,
            ))
            db.commit()
            return {"status": "success", "message": "تم أرشفة المستند."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Delete error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")