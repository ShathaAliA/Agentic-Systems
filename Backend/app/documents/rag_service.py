import os
from google import genai
from sqlalchemy import select
from app.documents.models import DocumentChunk
from sqlalchemy.orm import Session

# --- Lazy Loading ---
_embedding_model = None
def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        print("Loading Embedding Model for the first time...")
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2")
    return _embedding_model
    
# 2. Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# 3. Chunking
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text: return []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]

# 4. Embedding & Store
def process_and_store_chunks(db: Session, document_id: int, chunks: list):
    model = get_embedding_model() # نستخدم الدالة هنا
    for chunk in chunks:
        vector = model.encode(chunk).tolist()
        new_chunk = DocumentChunk(
            document_id=document_id,
            content=chunk,
            embedding=vector
        )
        db.add(new_chunk)
    db.commit()

# 5. Retrieval
def retrieve_relevant_chunks(db: Session, query: str, limit: int = 3):
    model = get_embedding_model() # نستخدم الدالة هنا
    query_vector = model.encode(query).tolist()
    
    stmt = select(DocumentChunk).order_by(
        DocumentChunk.embedding.cosine_distance(query_vector)
    ).limit(limit)
    
    results = db.execute(stmt).scalars().all()
    return [r.content for r in results]
# 6. Generation (مؤقت: رد بدون Gemini عشان نتأكد إن كل شي شغال)
def generate_answer(query: str, context: str) -> str:
    # نرجع أول 200 حرف من السياق عشان نتأكد إنه لقى المعلومة
    if context:
        return f"(إجابة تجريبية من السياق): {context[:200]}..."
    return "عذراً، لم أجد معلومات كافية للإجابة."