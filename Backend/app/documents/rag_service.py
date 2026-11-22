"""
RAG service: chunking, embedding, retrieval, and answer generation.

⚠️ `process_and_store_chunks` does NOT commit — caller (router) owns transaction.
"""

import os
import logging

from dotenv import load_dotenv

# مهم جدًا: يجب تحميل .env قبل قراءة LLM_PROVIDER
load_dotenv()

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


# LLM provider: groq | openai
PROVIDER = (os.getenv("LLM_PROVIDER") or "groq").lower()


_llm_client = None


def get_llm_client():
    """
    Lazy init for OpenAI-compatible providers.
    Supports: groq, openai
    """

    global _llm_client

    if _llm_client is not None:
        return _llm_client

    if PROVIDER == "groq":
        from openai import OpenAI

        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")

        _llm_client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
        )

    elif PROVIDER == "openai":
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        _llm_client = OpenAI(api_key=api_key)

    else:
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {PROVIDER}")

    return _llm_client


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        chunk = text[start:start + chunk_size].strip()

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks


def process_and_store_chunks(
    db: Session,
    document_id: int,
    chunks: list,
    version_number: int = 1,
    pages: list | None = None,
):
    """
    Store chunks for a specific document version.
    ⚠️ Does NOT commit — caller (router) owns the transaction.
    """

    model = get_embedding_model()

    if pages:
        for page in pages:
            page_number = page.get("page_number", 1)
            page_text = page.get("text", "")

            if not page_text.strip():
                continue

            page_chunks = chunk_text(page_text)

            for chunk in page_chunks:
                vector = model.encode(chunk).tolist()

                db.add(
                    DocumentChunk(
                        document_id=document_id,
                        content=chunk,
                        embedding=vector,
                        page_number=page_number,
                        version_number=version_number,
                    )
                )

    else:
        for chunk in chunks:
            vector = model.encode(chunk).tolist()

            db.add(
                DocumentChunk(
                    document_id=document_id,
                    content=chunk,
                    embedding=vector,
                    page_number=1,
                    version_number=version_number,
                )
            )

    db.flush()


def retrieve_relevant_chunks(
    db: Session,
    query: str,
    department_id: str,
    limit: int = 3,
    *,
    user_department_ids=(),
    user_role_ids=(),
):
    """Retrieve chunks from CURRENT approved version only, with audience filtering."""
    if not user_department_ids:
        return []
    
    from app.documents.audience_service import audience_predicate
    
    model = get_embedding_model()
    query_vector = model.encode(query).tolist()

    stmt = select(DocumentChunk).join(
        Document, Document.id == DocumentChunk.document_id
    ).where(
        Document.department_id == department_id,
        Document.status == "approved",
        audience_predicate(user_department_ids, user_role_ids),
        DocumentChunk.version_number == Document.version,
    ).order_by(
        DocumentChunk.embedding.cosine_distance(query_vector)
    ).limit(limit)

    results = db.execute(stmt).scalars().all()

    filtered_results = []

    for result in results:
        content = (result.content or "").strip()

        if len(content) <= 20:
            continue

        document = (
            db.query(Document)
            .filter(Document.id == result.document_id)
            .first()
        )

        if not document:
            continue

        filtered_results.append(
            {
                "content": content,
                "document_id": result.document_id,
                "document_name": document.filename,
                "chunk_id": result.id,
                "version": document.version,
                "version_number": result.version_number,
                "page_number": result.page_number,
            }
        )

    return filtered_results


def build_context(chunks: list) -> str:
    if not chunks:
        return ""

    context_parts = []

    for chunk in chunks:
        document_name = chunk.get("document_name", "مستند")
        page_number = chunk.get("page_number", 1)
        content = chunk.get("content", "")

        context_parts.append(
            f"[المستند: {document_name} | الصفحة: {page_number}]\n{content}"
        )

    return "\n\n".join(context_parts)


def generate_answer(query: str, context: str) -> str:
    prompt = f"""أنت مساعد ذكي لشركة وادي مكة. أجب على السؤال بناءً على السياق المرفق فقط.

القواعد:
- لا تخترع معلومات.
- إذا لم تجد الإجابة في السياق، قل: "لا أعرف".
- إذا وجدت تعارضًا بين المصادر، اذكر التعارض بوضوح.
- أجب بالعربية بشكل واضح ومختصر.

السياق:
{context}

السؤال:
{query}
"""

    client = get_llm_client()

    model = os.getenv("LLM_MODEL")

    if not model:
        if PROVIDER == "groq":
            model = "llama-3.3-70b-versatile"
        else:
            model = "gpt-4o-mini"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "أنت مساعد مؤسسي دقيق. أجب فقط من السياق المرفق.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content


# Server-side cost estimation (micro-USD per 1K tokens).
_COST_PER_1K_INPUT = {
    "groq": 0.000075,
    "openai": 0.000150,
}

_COST_PER_1K_OUTPUT = {
    "groq": 0.000300,
    "openai": 0.000600,
}


def estimate_cost_micro_usd(context: str, answer: str) -> int:
    input_tokens = max(len(context.split()), 1)
    output_tokens = max(len(answer.split()), 1)

    provider = PROVIDER or "groq"

    input_cost = _COST_PER_1K_INPUT.get(
        provider,
        _COST_PER_1K_INPUT["groq"],
    )

    output_cost = _COST_PER_1K_OUTPUT.get(
        provider,
        _COST_PER_1K_OUTPUT["groq"],
    )

    micro_usd = int(
        (input_tokens / 1000) * input_cost * 1_000_000
        + (output_tokens / 1000) * output_cost * 1_000_000
    )

    return max(micro_usd, 1)