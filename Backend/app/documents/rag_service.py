import os
from sentence_transformers import SentenceTransformer
from google import genai
from sqlalchemy import select
from app.documents.models import DocumentChunk
from sqlalchemy.orm import Session

# 1. تحميل نموذج التضمين
print("Loading Embedding Model...")
embedding_model = SentenceTransformer("Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2")

# 2. إعداد Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# 3. دالة التقسيم (Chunking)
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text: return []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size].strip())
        start += chunk_size - overlap
    return [c for c in chunks if c]

# 4. دالة التضمين والتخزين (Embedding & Store)
def process_and_store_chunks(db: Session, document_id: int, chunks: list):
    for chunk in chunks:
        vector = embedding_model.encode(chunk).tolist()
        new_chunk = DocumentChunk(
            document_id=document_id,
            content=chunk,
            embedding=vector
        )
        db.add(new_chunk)
    db.commit()

# 5. دالة البحث (Retrieval)
def retrieve_relevant_chunks(db: Session, query: str, limit: int = 3):
    # تحويل السؤال لمتجه
    query_vector = embedding_model.encode(query).tolist()
    
    # البحث في pgvector باستخدام المسافة (Cosine Distance)
    stmt = select(DocumentChunk).order_by(
        DocumentChunk.embedding.cosine_distance(query_vector)
    ).limit(limit)
    
    results = db.execute(stmt).scalars().all()
    return [r.content for r in results]

# 6. دالة توليد الإجابة (Generation)
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
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"حدث خطأ أثناء توليد الإجابة: {e}"