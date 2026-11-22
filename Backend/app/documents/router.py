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
from sqlalchemy import select, text
from app.usage.question_adapter import QuestionCommand
from app.usage.processor import ConfirmedTechnicalFailure
from app.usage.service import UsageError
from app.documents import provider_accounting
from app.documents.commands import Publish, Revision, VersionCommand, Question, Metadata, AudienceChange
from uuid import UUID
from app.documents import guardrails
from app.usage.external_results import PreparedQuestionResult
from app.conversation_store.conversations import ConversationService
from app.conversation_store.lifecycle import QuestionLifecycleService
from app.conversation_store.schema import Answer

from app.auth.dependencies import get_central_session as get_central_db
from app.documents.models import (
    Document,
    DocumentAuditLog,
    DocumentVersion,
    DocumentRevisionLog,
    DocumentChunk,
    DocumentAudience,
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
    "empty_extraction": "لا يمكن استخراج نص من هذا الملف (قد يكون فارغاً أو يحتاج OCR).",
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
        logger.error("Document operation failed")
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
        logger.warning("Document file rejected")
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

            extracted_text = result["extraction"].get("original_text", "") or ""

            # 1) أنشئ new_doc أولاً (بدون أرشفة القديم — أرشفة تحدث في approve)
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
            db.flush()  # للحصول على id

            # 2) version + audit
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

            # 3) chunks (بدون commit — flush فقط)
            chunks = rag_service.chunk_text(extracted_text)
            if chunks:
                rag_service.process_and_store_chunks(
                    db,
                    new_doc.id,
                    chunks,
                    version_number=new_doc.version,
                    pages=result["extraction"].get("pages"),
                )

            # 4) commit واحد (atomic) — لا أرشفة للقديم هنا
            db.commit()
            db.refresh(new_doc)

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
        logger.error("Document operation failed")
        # ⚠️ cleanup: احذف الملف النهائي إذا فشل DB commit
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Approve (archives previous approved — RAG stays available until then)
# ------------------------------------------------------------------
@router.post("/{document_id}/approve")
async def approve_document(
    document_id: int,
    body: Publish,
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
            # Serialize publication within this tenant before taking document
            # locks, including two different pending versions of the same name.
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                    {"scope": "document-publication:" + department_id})
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).with_for_update().first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            if doc.version != body.expected_version or doc.status != "pending_review":
                raise HTTPException(409, "document_version_conflict")
            # Names/roles are validated against Central; null is an explicit ALL.
            from app.models import Department, Role
            for audience in body.audiences:
                if ((audience.department_id is not None and central_db.get(Department, audience.department_id) is None)
                        or (audience.role_id is not None and central_db.get(Role, audience.role_id) is None)):
                    raise HTTPException(422, "unknown_document_audience")
            version = db.query(DocumentVersion).filter_by(
                document_id=doc.id, version_number=doc.version).one_or_none()
            if version is None:
                raise HTTPException(409, "document_version_missing")
            db.query(DocumentAudience).filter_by(document_id=doc.id).delete()
            for dept, role in {(a.department_id, a.role_id) for a in body.audiences}:
                db.add(DocumentAudience(document_id=doc.id, department_id=dept,
                    role_id=role, created_by=current_user.id))

            # ✅ NEW: archive previous approved docs with same filename (RAG stays on old until now)
            previous = db.query(Document).filter(
                Document.filename == doc.filename,
                Document.department_id == department_id,
                Document.id != doc.id,
                Document.status == "approved",
            ).all()
            for prev in previous:
                prev.status = "archived"
                prev_version = db.query(DocumentVersion).filter(
                    DocumentVersion.document_id == prev.id,
                    DocumentVersion.status == "approved",
                ).first()
                if prev_version:
                    prev_version.status = "archived"
                db.add(DocumentAuditLog(
                    document_id=prev.id,
                    action="archive",
                    performed_by=current_user.id,
                    version_number=prev.version,
                ))

            # اعتماد الجديد
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Revise
# ------------------------------------------------------------------
@router.post("/{document_id}/revise")
async def revise_document(
    document_id: int,
    body: Revision,
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
            ).with_for_update().first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            if doc.version != body.expected_version:
                raise HTTPException(409, "document_version_conflict")
            new_text = body.text
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


def _question_lifecycle(store, central_engine, settings):
    """Seam for tests: Core's lifecycle bound to the already authorized store."""
    return QuestionLifecycleService(ConversationService(store, central_engine), central_engine,
                                    quote_provider=lambda _command: settings.envelope)


# ------------------------------------------------------------------
# Ask (with U1 + persisted result)
# ------------------------------------------------------------------
@router.post("/ask")
def ask_question(
    request: Request,
    body: Question,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    department_id, query = body.department_id, body.question
    if not has_permission(central_db, current_user.id, "documents:read", department_id):
        raise HTTPException(403, "Permission denied")
    # One C06 result authority: the injected factory binds owner, conversation and department
    # and installs the source authorizer. No environment fallback and no tenant dual-write.
    factory = request.app.state.document_result_store_factory
    if factory is None:
        raise HTTPException(503, "document_result_integration_unavailable")
    try:
        store = factory(current_user.id, body.conversation_id, department_id)
        if (not isinstance(store, PostgresResultStore) or store.user_id != current_user.id
                or store.authorize is None):
            raise UsageError(503, "document_result_integration_unavailable")
        settings = provider_accounting.Settings.from_environment()
        central_engine = request.app.state.central_engine or central_db.get_bind()
        conversation_id = str(UUID(body.conversation_id))

        def context_for_user():
            with Session(central_engine) as session:
                return get_user_context(session, current_user.id, department_id)

        def process(ctx, command):
            departments, roles = context_for_user()
            with _open_department_session(current_user.id, department_id, "documents:read") as db:
                chunks = rag_service.retrieve_relevant_chunks(db, query, department_id,
                    user_department_ids=departments, user_role_ids=roles)
                allowed = [c for c in chunks if type(c.get("document_id")) is int
                    and user_can_access_document(db, c["document_id"], departments, roles)]
                from app.documents.source_authorization import source_for_chunk
                sources = [source_for_chunk(db, department_id, c) for c in allowed]
                payload = dict(question_request_id=ctx.request_id,
                    conversation_id=conversation_id, status="no_answer",
                    answer="لم يتم العثور على معلومات مسموح لك بالوصول إليها.",
                    answer_kind="system_notice", llm_called=False, sources=[],
                    unanswered_departments=[], handoff_available=True)
                if sources:
                    context = "\n\n".join(c["quote"] for c in sources)
                    if guardrails.prompt_size(query, context) + 512 > settings.input_limit:
                        raise ConfirmedTechnicalFailure()
                    def call(attempt_id):
                        answer, receipt = provider_accounting.generate(settings, query, context, attempt_id)
                        if guardrails.is_blocked(answer):
                            # Leaked instructions or unusable output: refuse, cite nothing, offer handoff. The
                            # attempt is already settled and counts as one question, never a silent refund.
                            payload.update(status="no_answer", answer=guardrails.BLOCKED_NOTICE,
                                answer_kind="system_notice", llm_called=True, sources=[])
                        else:
                            payload.update(status="answered", answer=answer, answer_kind="generated",
                                llm_called=True, sources=sources)
                        return receipt
                    ctx.perform("generation", "openai", settings.model, settings.envelope, call)
                validated = Answer.model_validate(payload).model_dump(mode="json")
                # save_once commits only to the independent Core result DB.
                return PreparedQuestionResult(payload["status"], validated)

        def authorize_content(user_id, cmd):
            if user_id != current_user.id or cmd.department_ids != (department_id,):
                return False
            with Session(central_engine) as session:
                return has_permission(session, user_id, "documents:read", department_id)

        result = _question_lifecycle(store, central_engine, settings).ask(
            conversation_id, body.request_key, query,
            authorize_content=authorize_content, process=process)
        if result["response"] is not None:
            return result["response"]
        if result["status"] == "refunded":
            raise HTTPException(503, "question_technical_failure_refunded")
        raise HTTPException(409, "question_processing_or_uncertain")
    except UsageError as error:
        raise HTTPException(error.status, error.code) from None
    except HTTPException:
        raise
    except ValidationError:
        raise HTTPException(422, "invalid_question_or_answer") from None
    except Exception:
        logger.error("Question operation failed")
        raise HTTPException(503, "question_service_unavailable") from None


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
        logger.error("Document operation failed")
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# Delete → archive
# ------------------------------------------------------------------
@router.post("/{document_id}/archive")
async def delete_document(
    document_id: int,
    body: VersionCommand,
    department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:archive", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        with _open_department_session(
            user_id=current_user.id,
            department_id=department_id,
            permission_code="documents:archive",
        ) as db:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.department_id == department_id,
            ).with_for_update().first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            if doc.version != body.expected_version:
                raise HTTPException(409, "document_version_conflict")
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: Get metadata
# ------------------------------------------------------------------
@router.get("/{document_id}/review")
async def review_document(
    document_id: int, department_id: str,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:update", department_id):
        raise HTTPException(403, "Permission denied")
    with _open_department_session(current_user.id, department_id, "documents:update") as db:
        doc = db.query(Document).filter_by(id=document_id, department_id=department_id).first()
        if doc is None:
            raise HTTPException(404, "document_not_found")
        return dict(document_id=doc.id, version=doc.version, status=doc.status,
                    extracted_text=doc.extracted_text or "",
                    # Codes and counts only, never the matched text: hidden characters, instruction-like text
                    content_warnings=guardrails.detect_injection_markers(doc.extracted_text))


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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: PATCH metadata
# ------------------------------------------------------------------
@router.patch("/{document_id}")
async def update_document_metadata(
    document_id: int,
    department_id: str,
    body: Metadata,
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
            ).with_for_update().first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            if doc.version != body.expected_version:
                raise HTTPException(409, "document_version_conflict")
            filename = body.filename
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: Replace document
# ------------------------------------------------------------------
@router.post("/{document_id}/version")
async def replace_document_version(
    document_id: int,
    file: UploadFile = File(...),
    department_id: str = Form(...),
    expected_version: int = Form(..., ge=1),
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

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        result = process_file(temp_path)
    except Exception as e:
        logger.error("Document operation failed")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=400, detail="فشل معالجة الملف. تأكد من أن الملف سليم وغير تالف.")

    if result["status"] != "accepted":
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reason = result.get("reason", "unknown")
        logger.warning("Document file rejected")
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
            ).with_for_update().first()
            if not doc:
                if os.path.exists(file_path):
                    os.remove(file_path)
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            if doc.version != expected_version:
                raise HTTPException(409, "document_version_conflict")
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
        logger.error("Document operation failed")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


# ------------------------------------------------------------------
# B2: Audiences
# ------------------------------------------------------------------
@router.post("/{document_id}/permissions")
async def add_document_audience(
    document_id: int,
    department_id: str,
    body: AudienceChange,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "documents:manage_permissions", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    audience_department_id, audience_role_id = body.department_id, body.role_id
    from app.models import Department, Role
    if ((audience_department_id is not None and central_db.get(Department, audience_department_id) is None)
            or (audience_role_id is not None and central_db.get(Role, audience_role_id) is None)):
        raise HTTPException(422, "unknown_document_audience")

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
            ).with_for_update().first()
            if not doc:
                raise HTTPException(status_code=404, detail="المستند غير موجود")

            if doc.version != body.expected_version:
                raise HTTPException(409, "document_version_conflict")
            existing = db.query(DocumentAudience).filter(
                DocumentAudience.document_id == document_id,
                DocumentAudience.department_id == audience_department_id,
                DocumentAudience.role_id == audience_role_id,
            ).with_for_update().first()
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
        logger.error("Document operation failed")
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
            ).with_for_update().first()
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")


@router.delete("/{document_id}/permissions/{audience_id}")
async def delete_document_audience(
    document_id: int,
    audience_id: int,
    department_id: str,
    body: VersionCommand,
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
            doc = db.query(Document).filter_by(id=document_id,
                department_id=department_id).with_for_update().first()
            if doc is None:
                raise HTTPException(404, "document_not_found")
            if doc.version != body.expected_version:
                raise HTTPException(409, "document_version_conflict")
            audience = db.query(DocumentAudience).filter(
                DocumentAudience.id == audience_id,
                DocumentAudience.document_id == document_id,
            ).with_for_update().first()
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
        logger.error("Document operation failed")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.")

# ------------------------------------------------------------------
# H6: Convert closed handoff to knowledge (pending_review)
# ------------------------------------------------------------------
from app.documents.handoff_knowledge_service import HandoffKnowledgeService
from sqlalchemy import text


@router.post("/api/handoffs/{handoff_id}/knowledge")
async def convert_handoff_to_knowledge(
    handoff_id: str,
    request_key: str,
    department_id: str,
    qa_payload: dict,
    central_db: Session = Depends(get_central_db),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(central_db, current_user.id, "handoffs:convert", department_id):
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        # ⚠️ Stub: يقرأ handoff من handoff_sessions (H0+H1)
        def handoff_lookup(hid: str) -> dict | None:
            row = central_db.execute(
                text("""
                    SELECT handoff_id, state, handler_user_id, department_id
                    FROM handoff_sessions
                    WHERE handoff_id = :hid
                """),
                {"hid": hid},
            ).fetchone()
            if row is None:
                return None
            return {
                "handoff_id": row[0],
                "state": row[1],
                "handler_user_id": row[2],
                "department_id": row[3],
            }

        service = HandoffKnowledgeService(
            handoff_lookup=handoff_lookup,
            dept_session_factory=lambda: _open_department_session(
                current_user.id, department_id, "handoffs:convert"
            ),
        )
        result = service.convert(
            user_id=current_user.id,
            handoff_id=handoff_id,
            request_key=request_key,
            department_id=department_id,
            qa_payload=qa_payload,
        )
        return {
            "status": result.status,
            "document_id": result.document_id,
            "document_version_id": result.document_version_id,
        }
    except UsageError as e:
        raise HTTPException(status_code=e.status, detail=e.code)
    except Exception:
        logger.exception("H6 convert error")
        raise HTTPException(status_code=500, detail="حدث خطأ غير متوقع.")