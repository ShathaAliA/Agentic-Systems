from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import shutil
import os
import uuid
import hashlib
import logging
from pydantic import ValidationError
from fastapi import Request
from sqlalchemy import select
from app.usage.question_adapter import QuestionCommand, execute_question
from app.usage.processor import ConfirmedTechnicalFailure
from app.usage.service import UsageError
from app.documents import result_store, provider_accounting

from app.auth.dependencies import get_central_session as get_central_db
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
def process_file(*args, **kwargs):
    from app.documents.file_processor import process_file as implementation
    return implementation(*args, **kwargs)
from app.documents import rag_service
from app.auth.dependencies import get_current_user
from app.models import User
from app.services.authorization import has_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["Documents"])

UPLOAD_DIR = "uploads"


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
    from app.database_router import authorized_department_session
    try:
        return authorized_department_session(
            user_id=user_id,
            department_id=department_id,
            permission_code=permission_code,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except ValueError:
        logger.warning("Department session rejected")
        raise HTTPException(status_code=403, detail="Department not available")
    except RuntimeError:
        logger.error("Department engine unavailable")
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
    os.makedirs(UPLOAD_DIR, exist_ok=True)
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
# Ask (with U1 + persisted result)
# ------------------------------------------------------------------
@router.post("/ask")
def ask_question(
    request: Request,
    query: str,
    department_id: str,
    request_key: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    try:
        settings = provider_accounting.Settings.from_environment()
        command = QuestionCommand(request_key=request_key, question=query,
            department_ids=(department_id,), context_revision=settings.context_revision)
        central_engine = request.app.state.central_engine or central_db.get_bind()

        def context_for_user():
            # New session after processing: no stale membership/role snapshot.
            with Session(central_engine) as session:
                return get_user_context(session, current_user.id, department_id)

        def process(ctx):
            departments, roles = context_for_user()
            with _open_department_session(current_user.id, department_id, "documents:read") as db:
                chunks = rag_service.retrieve_relevant_chunks(db, query, department_id)
                allowed = [c for c in chunks if type(c.get("document_id")) is int
                    and user_can_access_document(db, c["document_id"], departments, roles)]
                payload = {"status": "no_answer", "query": query,
                    "answer": "لم يتم العثور على معلومات مسموح لك بالوصول إليها.", "sources": []}
                if allowed:
                    context = "\n\n".join(c["content"] for c in allowed)
                    if len(provider_accounting.prompt_for(query, context).encode("utf-8")) + 512 > settings.input_limit:
                        raise ConfirmedTechnicalFailure()  # before authorizing/dispatching
                    def call(attempt_id):
                        answer, receipt = provider_accounting.generate(settings, query, context, attempt_id)
                        payload.update(status="answered", answer=answer, sources=[{
                            "document_id": c["document_id"], "document": c["document_name"],
                            "chunk_id": c["chunk_id"], "version_number": c["version_number"],
                            "location": c.get("page_number")} for c in allowed])
                        return receipt
                    ctx.perform("generation", "openai", settings.model, settings.envelope, call)
                result_store.save(db, current_user.id, ctx.request_id, payload)
                db.commit()
                return payload["status"]

        def load(user_id, identity):
            departments, roles = context_for_user()
            with _open_department_session(user_id, department_id, "documents:read") as db:
                return result_store.load(db, user_id, identity, department_id, departments, roles)

        def authorize_content(user_id, cmd):
            if (user_id != current_user.id or cmd.department_ids != (department_id,)
                    or cmd.context_revision != settings.context_revision):
                return False
            with Session(central_engine) as session:
                return has_permission(session, user_id, "documents:read", department_id)

        result = execute_question(central_engine, authenticated_user_id=current_user.id,
            command=command, estimated_micro_usd=settings.envelope,
            authorize_content=authorize_content,
            process=process, load_response=load)
        if result.response is not None:
            return {**result.response, "request_key": request_key, "replayed": result.receipt.replayed}
        if result.receipt.state == "refunded":
            raise HTTPException(503, "تعذر إكمال الطلب. تم إرجاع الرصيد.")
        raise HTTPException(409, "الطلب قيد المعالجة أو يحتاج التحقق؛ لم يُعَد تنفيذه.")
    except UsageError as error:
        raise HTTPException(error.status, error.code) from None
    except HTTPException:
        raise
    except ValidationError:
        raise HTTPException(422, "Invalid question data") from None
    except Exception:
        raise HTTPException(503, "Question service unavailable") from None


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

    user_depts, user_roles = get_user_context(central_db, current_user.id, department_id)

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

    user_depts, user_roles = get_user_context(central_db, current_user.id, department_id)

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
# B2: Replace document
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
            detail="حدد قسماً أو دوراً (أو كلاهما).",
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
