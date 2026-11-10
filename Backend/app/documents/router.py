from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import shutil
import os
import uuid
import hashlib
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


REJECTION_MESSAGES = {
    "unsupported_file_type": "نوع الملف غير مدعوم.",
    "extension_mismatch": "امتداد الملف لا يطابق محتواه. يرجى التأكد من الملف.",
    "password_required": "الملف مشفر بكلمة مرور. يرجى إزالة الحماية أولاً.",
    "too_many_pages": "الملف يتجاوز 100 صفحة. يرجى تقسيمه إلى ملفات أصغر.",
    "invalid_format": "الملف تالف أو غير قابل للقراءة.",
    "no_text_extraction_without_ocr": "لا يمكن استخراج نص من هذا الملف (قد يحتاج OCR).",
    "extraction_failed": "تعذر استخراج النص من الملف.",
}


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
        safe_message = REJECTION_MESSAGES.get(
            reason, "الملف غير مدعوم أو تالف. يرجى التأكد من نوع الملف."
        )
        raise HTTPException(status_code=400, detail=safe_message)

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
                    version_number=existing_doc.version,
                ))
                existing_version = db.query(DocumentVersion).filter(
                    DocumentVersion.document_id == existing_doc.id,
                    DocumentVersion.status == "approved",
                ).first()
                if existing_version:
                    existing_version.status = "archived"
                db.commit()

            extracted_text = result["extraction"].get("original_text", "") or ""

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
                version_number=new_doc.version,
            ))
            db.commit()

            chunks = rag_service.chunk_text(extracted_text)
            if chunks:
                rag_service.process_and_store_chunks(
                    db,
                    new_doc.id,
                    chunks,
                    version_number=new_doc.version,
                    pages=result["extraction"].get("pages"),
                )

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
                version_number=doc.version,
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

            old_version = doc.version
            new_version_number = old_version + 1

            db.add(DocumentRevisionLog(
                document_id=doc.id,
                old_text=doc.extracted_text,
                new_text=new_text,
                revised_by=current_user.id,
                from_version=old_version,
                to_version=new_version_number,
            ))

            doc.extracted_text = new_text
            doc.version = new_version_number
            doc.status = "pending_review"

            db.add(DocumentVersion(
                document_id=doc.id,
                version_number=new_version_number,
                status="pending",
            ))

            new_chunks = rag_service.chunk_text(new_text)
            if new_chunks:
                rag_service.process_and_store_chunks(
                    db,
                    doc.id,
                    new_chunks,
                    version_number=new_version_number,
                    pages=None,
                )

            db.add(DocumentAuditLog(
                document_id=doc.id,
                action="revise",
                performed_by=current_user.id,
                version_number=new_version_number,
            ))

            db.commit()
            return {
                "status": "success",
                "message": "تم التصحيح. الإصدار الجديد بانتظار المراجعة.",
                "new_version": new_version_number,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Revise error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Ask (with U1 idempotency)
# ------------------------------------------------------------------

@router.post("/ask")
async def ask_question(
    query: str,
    department_id: str,
    request_key: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:read", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    fingerprint = hashlib.sha256(
        f"{query}|{department_id}".encode("utf-8")
    ).hexdigest()

    user_depts, user_roles = get_user_context(central_db, current_user.id)

    from app.usage.integration import run_once, AccountingContext, ProviderReceipt
    from app.usage import service as usage_service
    from app.database import engine as central_engine

    def processor(ctx: AccountingContext) -> str:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:read",
        ) as db:
            context_chunks = rag_service.retrieve_relevant_chunks(db, query, department_id)
            if not context_chunks:
                return "no_answer"

            allowed_chunks = []
            for c in context_chunks:
                doc_id = c.get("document_id")
                if doc_id is None or user_can_access_document(db, doc_id, user_depts, user_roles):
                    allowed_chunks.append(c)

            if not allowed_chunks:
                return "no_answer"

            context = "\n\n".join([c["content"] for c in allowed_chunks])

            provider = rag_service.PROVIDER
            model = "gpt-4o-mini" if provider == "openai" else "gemini-3.8-flash"
            attempt_key = f"ask:{request_key}"

            def callback(attempt_id):
                try:
                    answer = rag_service.generate_answer(query, context)
                    # Use server-computed cost from actual provider usage.
                    # Provider token counts are advisory; cost is deterministic.
                    from app.documents.rag_service import estimate_cost_micro_usd
                    cost = estimate_cost_micro_usd(context, answer)
                    return ProviderReceipt(
                        key=f"receipt:{attempt_id}",
                        cost_micro_usd=cost,
                        input_tokens=len(context.split()),
                        output_tokens=len(answer.split()),
                    )
                except Exception as e:
                    # Provider failure AFTER authorization → uncertain (no refund).
                    logger.exception("LLM provider failed: %s", e)
                    raise

            ctx.perform(attempt_key, provider, model, estimate_cost_micro_usd(context, ""), callback)
            return "answered"

    try:
        result = run_once(
            engine=central_engine,
            user_id=current_user.id,
            request_key=request_key,
            fingerprint=fingerprint,
            estimated_micro_usd=estimate_cost_micro_usd("", ""),
            processor=processor,
        )
    except usage_service.UsageError as e:
        raise HTTPException(status_code=e.status, detail=e.code)
    except Exception as e:
        logger.exception("U1 error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")

    if result.state == "completed":
        if result.outcome == "answered":
            return _build_answer_response(
                query, request_key, department_id, current_user.id, user_depts, user_roles
            )
        elif result.outcome == "no_answer":
            return {
                "query": query,
                "answer": "لم يتم العثور على معلومات مسموح لك بالوصول إليها.",
                "sources": [],
                "request_key": request_key,
                "replayed": result.replayed,
            }
    elif result.state == "refunded":
        return {
            "query": query,
            "answer": "تعذر إكمال الطلب. تم إرجاع الرصيد.",
            "sources": [],
            "request_key": request_key,
            "replayed": result.replayed,
        }

    raise HTTPException(
        status_code=409,
        detail=f"الطلب قيد المعالجة (state={result.state}). يرجى إعادة المحاولة.",
    )


def _build_answer_response(query, request_key, department_id, user_id, user_depts, user_roles):
    with _open_department_session(
        user_id=user_id,
        department_id=department_id,
        permission_code="documents:read",
    ) as db:
        context_chunks = rag_service.retrieve_relevant_chunks(db, query, department_id)
        allowed = [
            c for c in context_chunks
            if c.get("document_id") is None
            or user_can_access_document(db, c["document_id"], user_depts, user_roles)
        ]
        if not allowed:
            return {
                "query": query,
                "answer": "لم يتم العثور على معلومات مسموح لك بالوصول إليها.",
                "sources": [],
                "request_key": request_key,
                "replayed": True,
            }
        context = "\n\n".join([c["content"] for c in allowed])
        answer = rag_service.generate_answer(query, context)
        return {
            "query": query,
            "answer": answer,
            "sources": [
                {
                    "document": c["document_name"],
                    "chunk_id": c["chunk_id"],
                    "version": c.get("version"),
                    "version_number": c.get("version_number"),
                    "location": c.get("page_number"),
                }
                for c in allowed
            ],
            "request_key": request_key,
            "replayed": True,
        }


# ------------------------------------------------------------------
# List
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
# Download
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
                version_number=doc.version,
            ))
            db.commit()
            return {"status": "success", "message": "تم أرشفة المستند."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Delete error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: Get metadata
# ------------------------------------------------------------------
@router.get("/{document_id}")
async def get_document(
    document_id: int,
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
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")
            if not user_can_access_document(db, doc.id, user_depts, user_roles):
                raise HTTPException(status_code=403, detail="Permission denied")

            return {
                "id": doc.id,
                "filename": doc.filename,
                "file_type": doc.file_type,
                "status": doc.status,
                "version": doc.version,
                "upload_date": doc.upload_date,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Get document error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: PATCH metadata
# ------------------------------------------------------------------
@router.patch("/{document_id}")
async def update_document_metadata(
    document_id: int,
    department_id: str,
    filename: str | None = None,
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

            if filename is not None:
                if not filename.strip():
                    raise HTTPException(status_code=400, detail="اسم الملف لا يمكن أن يكون فارغاً")
                doc.filename = filename.strip()

            db.add(DocumentAuditLog(
                document_id=doc.id,
                action="update_metadata",
                performed_by=current_user.id,
                version_number=doc.version,
            ))
            db.commit()
            return {
                "status": "success",
                "message": "تم تحديث بيانات المستند.",
                "id": doc.id,
                "filename": doc.filename,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Update metadata error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: Replace document (new version)
# ------------------------------------------------------------------
@router.post("/{document_id}/version")
async def replace_document_version(
    document_id: int,
    file: UploadFile = File(...),
    department_id: str = Form(...),
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:update", department_id):
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
        logger.exception("Replace error processing file %s: %s", temp_path, e)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=400, detail="فشل معالجة الملف. تأكد من أن الملف سليم وغير تالف.")

    if result["status"] != "accepted":
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reason = result.get("reason", "unknown")
        logger.warning("Replace rejected: %s (file=%s)", reason, file.filename)
        safe_message = REJECTION_MESSAGES.get(
            reason, "الملف غير مدعوم أو تالف. يرجى التأكد من نوع الملف."
        )
        raise HTTPException(status_code=400, detail=safe_message)

    safe_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    shutil.move(temp_path, file_path)

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
                if os.path.exists(file_path):
                    os.remove(file_path)
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            old_version = doc.version
            new_version_number = old_version + 1
            extracted_text = result["extraction"].get("original_text", "") or ""

            db.add(DocumentRevisionLog(
                document_id=doc.id,
                old_text=doc.extracted_text,
                new_text=extracted_text,
                revised_by=current_user.id,
                from_version=old_version,
                to_version=new_version_number,
            ))

            doc.file_path = file_path
            doc.file_type = file_ext
            doc.extracted_text = extracted_text
            doc.version = new_version_number
            doc.status = "pending_review"

            db.add(DocumentVersion(
                document_id=doc.id,
                version_number=new_version_number,
                status="pending",
            ))

            new_chunks = rag_service.chunk_text(extracted_text)
            if new_chunks:
                rag_service.process_and_store_chunks(
                    db, doc.id, new_chunks,
                    version_number=new_version_number,
                    pages=result["extraction"].get("pages"),
                )

            db.add(DocumentAuditLog(
                document_id=doc.id,
                action="replace",
                performed_by=current_user.id,
                version_number=new_version_number,
            ))
            db.commit()

            return {
                "status": "success",
                "message": "تم استبدال الملف. الإصدار الجديد بانتظار المراجعة.",
                "new_version": new_version_number,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Replace DB error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: Audiences
# ------------------------------------------------------------------
@router.post("/{document_id}/permissions")
async def add_document_audience(
    document_id: int,
    department_id: str,
    audience_department_id: str | None = None,
    audience_role_id: str | None = None,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:manage_permissions", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    if audience_department_id is None and audience_role_id is None:
        raise HTTPException(
            status_code=400,
            detail="حدد قسماً أو دوراً (أو كلاهما). لاستخدام (NULL,NULL) استخدم endpoint منفصل.",
        )

    try:
        from app.documents.models import DocumentAudience
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:manage_permissions",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            existing = db.query(DocumentAudience).filter(
                DocumentAudience.document_id == document_id,
                DocumentAudience.department_id == audience_department_id,
                DocumentAudience.role_id == audience_role_id,
            ).first()
            if existing:
                return {"status": "exists", "audience_id": existing.id}

            audience = DocumentAudience(
                document_id=document_id,
                department_id=audience_department_id,
                role_id=audience_role_id,
                created_by=current_user.id,
            )
            db.add(audience)
            db.add(DocumentAuditLog(
                document_id=doc.id,
                action="add_audience",
                performed_by=current_user.id,
                version_number=doc.version,
            ))
            db.commit()
            db.refresh(audience)
            return {"status": "success", "audience_id": audience.id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Add audience error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


@router.get("/{document_id}/permissions")
async def list_document_audiences(
    document_id: int,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:manage_permissions", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        from app.documents.models import DocumentAudience
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:manage_permissions",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            audiences = db.query(DocumentAudience).filter(
                DocumentAudience.document_id == document_id,
            ).all()

            return {
                "count": len(audiences),
                "audiences": [
                    {
                        "id": a.id,
                        "department_id": a.department_id,
                        "role_id": a.role_id,
                        "created_at": a.created_at,
                    }
                    for a in audiences
                ],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("List audiences error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


@router.delete("/{document_id}/permissions/{audience_id}")
async def delete_document_audience(
    document_id: int,
    audience_id: int,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:manage_permissions", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        from app.documents.models import DocumentAudience
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:manage_permissions",
        ) as db:
            audience = db.query(DocumentAudience).filter(
                DocumentAudience.id == audience_id,
                DocumentAudience.document_id == document_id,
            ).first()
            if not audience:
                raise HTTPException(status_code=404, detail="الجمهور غير موجود")

            db.delete(audience)
            db.add(DocumentAuditLog(
                document_id=document_id,
                action="delete_audience",
                performed_by=current_user.id,
                version_number=None,
            ))
            db.commit()
            return {"status": "success", "message": "تم حذف الجمهور."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Delete audience error: %s", e)
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")