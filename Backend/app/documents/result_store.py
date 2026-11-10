"""Tenant-owned immutable answers. Revalidate all cited sources at delivery."""
from sqlalchemy import select
from app.documents.models import Document, QuestionResult
from app.documents.audience_service import user_can_access_document
from app.usage.service import UsageError


def save(session, user_id, request_id, payload):
    session.add(QuestionResult(request_id=request_id, user_id=user_id, payload=payload))
    session.flush()


def load(session, user_id, request_id, department_id, departments, roles):
    row = session.scalar(select(QuestionResult).where(
        QuestionResult.request_id == request_id, QuestionResult.user_id == user_id))
    if row is None:
        return None
    payload = row.payload
    for source in payload.get("sources", []):
        identity = source.get("document_id")
        doc = session.get(Document, identity) if type(identity) is int else None
        if (doc is None or doc.department_id != department_id or doc.status != "approved"
                or doc.version != source.get("version_number")
                or not user_can_access_document(session, doc.id, departments, roles)):
            raise UsageError(403, "question_source_access_revoked")
    if payload.get("answer") and not payload.get("sources") and payload.get("status") != "no_answer":
        raise UsageError(403, "question_source_access_revoked")
    return payload
