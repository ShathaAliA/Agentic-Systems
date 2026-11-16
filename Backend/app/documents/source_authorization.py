"""Trusted single-tenant source verification; no cross-tenant broker or DSN lookup."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.conversation_store.schema import Answer
from app.documents.models import Document, DocumentChunk, DocumentVersion
from app.documents.audience_service import get_user_context, user_can_access_document
from app.usage.service import UsageError


def source_for_chunk(db, department_id, chunk):
    doc = db.get(Document, chunk["document_id"])
    version = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == chunk["document_id"],
        DocumentVersion.version_number == chunk["version_number"]))
    stored = db.get(DocumentChunk, chunk["chunk_id"])
    if (doc is None or doc.department_id != department_id or doc.status != "approved"
            or version is None or version.status != "approved"
            or doc.version != chunk["version_number"] or stored is None
            or stored.document_id != doc.id or stored.version_number != doc.version
            or stored.content != chunk["content"]):
        raise UsageError(403, "question_source_access_revoked")
    # Current schema cannot distinguish a genuine page from legacy fallback=1
    # or a reviewer correction. Cite the exact stored chunk without inventing a
    # physical PDF page/slide/cell. Rich extraction locations need a versioned
    # location column and a separate additive tenant migration.
    location = {"kind": "reviewed_text", "section": f"chunk:{stored.id}"}
    return dict(department_id=department_id, document_id=doc.id,
        version_id=str(version.id), version_number=version.version_number,
        name=doc.filename, quote=stored.content, location=location)


class DocumentSourceAuthorizer:
    """Server-created, bound owner/scope; every load opens fresh authorization.

    tenant_session must be authorized_department_session or an equivalent trusted
    checked factory. This class never opens another tenant based on payload data.
    """
    def __init__(self, central_engine, tenant_session, *, user_id, department_id, conversation_id):
        self.central_engine, self.tenant_session = central_engine, tenant_session
        self.user_id, self.department_id = user_id, department_id
        self.conversation_id = conversation_id

    def __call__(self, user_id, payload):
        if user_id != self.user_id:
            return False
        answer = Answer.model_validate(payload)
        if (answer.conversation_id != self.conversation_id
                or any(d != self.department_id for d in answer.unanswered_departments)):
            return False
        with Session(self.central_engine) as central:
            departments, roles = get_user_context(central, user_id, self.department_id)
            if not departments:
                return False
            with self.tenant_session(user_id=user_id, department_id=self.department_id,
                    permission_code="documents:read") as db:
                for source in answer.sources:
                    if source.department_id != self.department_id:
                        return False
                    doc = db.get(Document, source.document_id)
                    if doc is None or not user_can_access_document(db, doc.id, departments, roles):
                        return False
                    # Exact source equality covers name, version identity, quote and location.
                    chunks = db.scalars(select(DocumentChunk).where(
                        DocumentChunk.document_id == doc.id,
                        DocumentChunk.version_number == source.version_number,
                        DocumentChunk.content == source.quote)).all()
                    if not any(source.model_dump(mode="json") == source_for_chunk(db,
                        self.department_id, dict(document_id=doc.id, version_number=c.version_number,
                            chunk_id=c.id, content=c.content)) for c in chunks):
                        return False
        return True
