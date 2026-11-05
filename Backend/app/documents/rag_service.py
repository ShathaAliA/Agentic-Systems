import os
from google import genai
from sqlalchemy import select
from app.documents.models import DocumentChunk, Document
from sqlalchemy.orm import Session

_embedding_model = None
def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        print("Loading Embedding Model for the first time...")
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2")
    return _embedding_model

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text: return []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]

def process_and_store_chunks(db: Session, document_id: int, chunks: list):
    model = get_embedding_model()
    for chunk in chunks:
        vector = model.encode(chunk).tolist()
        new_chunk = DocumentChunk(
            document_id=document_id,
            content=chunk,
            embedding=vector
        )
        db.add(new_chunk)
    db.commit()

def retrieve_relevant_chunks(db: Session, query: str, department_id: str, limit: int = 3):
    model = get_embedding_model()
    query_vector = model.encode(query).tolist()
    
    # البحث المفلتر: نربط بالجدول Document عشان نفلتر بالقسم
    stmt = select(DocumentChunk).join(Document).where(
        Document.department_id == department_id
    ).order_by(
        DocumentChunk.embedding.cosine_distance(query_vector)
    ).limit(limit)
    
    results = db.execute(stmt).scalars().all()
    return [r.content for r in results]

def generate_answer(query: str, context: str) -> str:
    if not gemini_client:
        return "خطأ: مفتاح Gemini API غير موجود في ملف .env"
        
    prompt = f"""أنت مساعد ذكي لشركة وادي مكة. أجب على السؤال بناءً على السياق المرفق فقط.
إذا لم تجد الإجابة في السياق، قل "لا أعرف" ولا تحاول التخمين.

السياق:
{context}

السؤال:
{query}
"""
    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.8-flash", # استخدمنا النموذج الأحدث
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"حدث خطأ أثناء توليد الإجابة: {e}"