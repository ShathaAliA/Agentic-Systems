from sentence_transformers import SentenceTransformer
from app.documents.models import DocumentChunk
from sqlalchemy.orm import Session

# تحميل النموذج
model = SentenceTransformer("Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2")

def process_and_store_chunks(db: Session, document_id: int, chunks: list):
    """تحويل القطع لمتجهات وتخزينها"""
    for chunk in chunks:
        # تحويل النص لمتجه
        vector = model.encode(chunk).tolist()
        
        # حفظ في قاعدة البيانات
        new_chunk = DocumentChunk(
            document_id=document_id,
            content=chunk,
            embedding=vector
        )
        db.add(new_chunk)
    db.commit()