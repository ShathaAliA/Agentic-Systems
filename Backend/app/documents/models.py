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
    department_id = Column(String(50), nullable=True) # تعديل: String
    upload_date = Column(DateTime(timezone=True), server_default=func.now())

class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False) # إرجاع document_id
    content = Column(Text, nullable=False)
    embedding = Column(Vector(768))