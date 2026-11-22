from sqlalchemy import create_engine, text

# رابط قاعدة البيانات المحلية wadi_db
TEST_DB_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5435/wadi_db"

engine = create_engine(TEST_DB_URL)

with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS handoff_knowledge (
            id VARCHAR(36) PRIMARY KEY,
            handoff_id VARCHAR(255) NOT NULL,
            request_key VARCHAR(255) NOT NULL,
            document_id INTEGER NOT NULL,
            document_version_id INTEGER NOT NULL,
            created_by VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_handoff_request UNIQUE (handoff_id, request_key)
        );
    """))
    print("تم إنشاء جدول handoff_knowledge بنجاح تام!")