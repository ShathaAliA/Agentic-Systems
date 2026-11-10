import os
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import DocumentChunk, Document

logger = logging.getLogger(__name__)

_embedding_model = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading embedding model (first use)...")
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(
            "Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2"
        )
    return _embedding_model


PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]


def process_and_store_chunks(
    db: Session,
    document_id: int,
    chunks: list,
    version_number: int = 1,
    pages: list | None = None,
):
    """
    Store chunks for a specific document version.

    If `pages` (list of {page_number, text}) is provided, chunks are
    attributed to their real page/slide/sheet. Otherwise, chunks are
    attributed to page 1 (legacy fallback).
    """
    model = get_embedding_model()

    if pages:
        # Split each page separately → preserve real location
        for page in pages:
            page_num = page.get("page_number", 1)
            page_text = page.get("text", "")
            if not page_text.strip():
                continue
            page_chunks = chunk_text(page_text)
            for chunk in page_chunks:
                vector = model.encode(chunk).tolist()
                db.add(DocumentChunk(
                    document_id=document_id,
                    content=chunk,
                    embedding=vector,
                    page_number=page_num,
                    version_number=version_number,
                ))
    else:
        # Legacy fallback: no page info → attribute to page 1
        for chunk in chunks:
            vector = model.encode(chunk).tolist()
            db.add(DocumentChunk(
                document_id=document_id,
                content=chunk,
                embedding=vector,
                page_number=1,
                version_number=version_number,
            ))

    db.commit()


def retrieve_relevant_chunks(db: Session, query: str, department_id: str, limit: int = 3):
    """Retrieve chunks from CURRENT approved version only."""
    model = get_embedding_model()
    query_vector = model.encode(query).tolist()

    stmt = select(DocumentChunk).join(
        Document, Document.id == DocumentChunk.document_id
    ).where(
        Document.department_id == department_id,
        Document.status == "approved",
        DocumentChunk.version_number == Document.version,
    ).order_by(
        DocumentChunk.embedding.cosine_distance(query_vector)
    ).limit(limit)

    results = db.execute(stmt).scalars().all()

    filtered_results = []
    for r in results:
        if len(r.content.strip()) > 20:
            doc = db.query(Document).filter(Document.id == r.document_id).first()
            if doc:
                filtered_results.append({
                    "content": r.content,
                    "document_id": r.document_id,
                    "document_name": doc.filename,
                    "chunk_id": r.id,
                    "version": doc.version,
                    "version_number": r.version_number,
                    "page_number": r.page_number,
                })

    return filtered_results


def generate_answer(query: str, context: str) -> str:
    """Unmetered legacy calls are disabled; use provider_accounting through U1."""
    raise RuntimeError("Use the metered question adapter")
