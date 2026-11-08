from app.models import Base
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, func
from pgvector.sqlalchemy import Vector

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=True)
    extracted_text = Column(Text, nullable=True)
    department_id = Column(String(50), nullable=True)
    status = Column(String(20), default="pending_review")
    version = Column(Integer, default=1)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())

class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    content = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True) # <-- أضفنا هذا
    embedding = Column(Vector(768))

class DocumentAuditLog(Base):
    __tablename__ = "document_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    action = Column(String(50), nullable=False) # upload, approve, archive, revise
    performed_by = Column(String(36), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False) # approved, archived, pending
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class DocumentRevisionLog(Base):
    __tablename__ = "document_revision_logs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    old_text = Column(Text, nullable=True)
    new_text = Column(Text, nullable=False)
    revised_by = Column(String(36), nullable=False)
    revised_at = Column(DateTime(timezone=True), server_default=func.now())

class DocumentAudience(Base):
    __tablename__ = "document_audiences"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    # NULL department_id = "all departments"
    department_id = Column(String(36), nullable=True)
    # NULL role_id = "all roles"
    role_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(36), nullable=False)    