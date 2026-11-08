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

PROVIDER = os.getenv("LLM_PROVIDER", "gemini") # <-- أضفنا هذا

if PROVIDER == "openai":
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
elif PROVIDER == "gemini":
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

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
            embedding=vector,
            page_number=1 # <-- نحط 1 مؤقتاً
        )
        db.add(new_chunk)
    db.commit()

def retrieve_relevant_chunks(db: Session, query: str, department_id: str, limit: int = 3):
    model = get_embedding_model()
    query_vector = model.encode(query).tolist()
    
    stmt = select(DocumentChunk).join(Document).where(
        Document.department_id == department_id,
        Document.status == "approved"
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
                    "document_name": doc.filename,
                    "chunk_id": r.id,
                    "version": doc.version,
                    "page_number": r.page_number # <-- نرجع الموضع
                })
    
    return filtered_results

def generate_answer(query: str, context: str) -> str:
    prompt = f"""أنت مساعد ذكي لشركة وادي مكة. أجب على السؤال بناءً على السياق المرفق فقط.
إذا لم تجد الإجابة في السياق، قل "لا أعرف" ولا تحاول التخمين.
إذا وجدت تعارضاً بين المصادر، اذكر التعارض بوضوح ولا تفضل أحدها.

السياق:
{context}

السؤال:
{query}
"""
    try:
        if PROVIDER == "openai":
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content
        else:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt
            )
            return response.text
    except Exception as e:
        return f"حدث خطأ أثناء توليد الإجابة: {e}"