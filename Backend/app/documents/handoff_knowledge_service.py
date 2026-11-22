"""H6: convert closed handoff to Documents knowledge (idempotent)."""
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.conversation_store.handoff_knowledge import HandoffKnowledgeStore
from app.documents.models import Document, DocumentVersion
from app.usage.service import UsageError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConvertResult:
    status: str  # "created" | "exists"
    document_id: int
    document_version_id: int


class HandoffKnowledgeService:
    """Convert a closed handoff to a pending_review document."""

    def __init__(self, *, handoff_lookup, dept_session_factory):
        """
        handoff_lookup: callable(handoff_id) -> handoff dict (state, handler_user_id, department_id)
        dept_session_factory: context manager yielding Session on department DB
        """
        self.handoff_lookup = handoff_lookup
        self.dept_session_factory = dept_session_factory

    def convert(
        self,
        *,
        user_id: str,
        handoff_id: str,
        request_key: str,
        department_id: str,
        qa_payload: dict,
    ) -> ConvertResult:
        # 1) Lookup handoff (via HandoffService — H2)
        handoff = self.handoff_lookup(handoff_id)
        if handoff is None:
            raise UsageError(404, "handoff_not_found")
        if handoff.get("state") != "closed":
            raise UsageError(409, "handoff_not_closed")
        if handoff.get("handler_user_id") != user_id:
            raise UsageError(403, "not_handoff_handler")
        if handoff.get("department_id") != department_id:
            raise UsageError(403, "department_mismatch")

        # 2) Validate qa_payload
        question = (qa_payload.get("question") or "").strip()
        answer = (qa_payload.get("answer") or "").strip()
        if not question or not answer:
            raise UsageError(422, "invalid_qa_payload")

        # 3) Open department session
        with self.dept_session_factory() as db:
            store = HandoffKnowledgeStore(db)

            # 3a) Idempotency (C06)
            existing = store.find(handoff_id, request_key)
            if existing is not None:
                return ConvertResult(
                    status="exists",
                    document_id=existing.document_id,
                    document_version_id=existing.document_version_id,
                )

            # 3b) Create Document (pending_review) via Documents path
            text_payload = f"# سؤال\n{question}\n\n# إجابة\n{answer}\n"
            doc = Document(
                filename=f"handoff-{handoff_id[:8]}.md",
                file_path="",  # سيُملأ من الـ file upload (H6 — يمكن stub)
                file_type=".md",
                extracted_text=text_payload,
                department_id=department_id,
                status="pending_review",
                version=1,
            )
            db.add(doc)
            db.flush()

            version = DocumentVersion(
                document_id=doc.id,
                version_number=1,
                status="pending",
            )
            db.add(version)
            db.flush()

            # 3c) Record link in C06
            record = store.insert(
                handoff_id=handoff_id,
                request_key=request_key,
                document_id=doc.id,
                document_version_id=version.id,
                created_by=user_id,
            )
            db.commit()

            return ConvertResult(
                status="created",
                document_id=record.document_id,
                document_version_id=record.document_version_id,
            )