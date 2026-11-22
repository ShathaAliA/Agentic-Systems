"""C06 handoff_knowledge model + store methods (H6)."""
from dataclasses import dataclass
from datetime import datetime
import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.usage.service import UsageError


@dataclass(frozen=True)
class HandoffKnowledgeRecord:
    id: str
    handoff_id: str
    request_key: str
    document_id: int
    document_version_id: int
    created_by: str
    created_at: datetime


class HandoffKnowledgeStore:
    """Insert-only store for handoff→knowledge links."""

    def __init__(self, session: Session):
        self.session = session

    def find(self, handoff_id: str, request_key: str) -> HandoffKnowledgeRecord | None:
        row = self.session.execute(
            text("""
                SELECT id, handoff_id, request_key, document_id,
                       document_version_id, created_by, created_at
                FROM handoff_knowledge
                WHERE handoff_id = :hid AND request_key = :rk
            """),
            {"hid": handoff_id, "rk": request_key},
        ).fetchone()
        if row is None:
            return None
        return HandoffKnowledgeRecord(*row)

    def insert(
        self,
        *,
        handoff_id: str,
        request_key: str,
        document_id: int,
        document_version_id: int,
        created_by: str,
    ) -> HandoffKnowledgeRecord:
        new_id = str(uuid.uuid4())
        self.session.execute(
            text("""
                INSERT INTO handoff_knowledge
                    (id, handoff_id, request_key, document_id, document_version_id, created_by)
                VALUES
                    (:id, :hid, :rk, :did, :dvid, :uid)
            """),
            {
                "id": new_id,
                "hid": handoff_id,
                "rk": request_key,
                "did": document_id,
                "dvid": document_version_id,
                "uid": created_by,
            },
        )
        self.session.flush()
        return self.find(handoff_id, request_key)  # type: ignore[return-value]