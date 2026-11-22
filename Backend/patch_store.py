path = "app/conversation_store/postgres.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

if "def find_handoff_knowledge" not in content:
    methods = """
    def find_handoff_knowledge(self, handoff_id: str, request_key: str):
        \"\"\"Find existing handoff knowledge mapping.\"\"\"
        with self.engine.begin() as conn:
            row = conn.execute(
                text(\"\"\"
                    SELECT id, handoff_id, request_key, document_id, document_version_id, created_by, created_at
                    FROM handoff_knowledge
                    WHERE handoff_id = :handoff_id AND request_key = :request_key
                \"\"\"),
                {"handoff_id": handoff_id, "request_key": request_key}
            ).mappings().first()
            return row

    def create_handoff_knowledge(self, *, handoff_id: str, request_key: str, document_id: int, document_version_id: int, created_by: str):
        \"\"\"Create a new handoff knowledge mapping idempotently.\"\"\"
        import uuid
        record_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                text(\"\"\"
                    INSERT INTO handoff_knowledge (id, handoff_id, request_key, document_id, document_version_id, created_by)
                    VALUES (:id, :handoff_id, :request_key, :document_id, :document_version_id, :created_by)
                    ON CONFLICT (handoff_id, request_key) DO NOTHING
                \"\"\"),
                {
                    "id": record_id,
                    "handoff_id": handoff_id,
                    "request_key": request_key,
                    "document_id": document_id,
                    "document_version_id": document_version_id,
                    "created_by": created_by,
                }
            )
        return self.find_handoff_knowledge(handoff_id, request_key)
"""
    content += "\n" + methods
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("تم تحديث PostgresResultStore بنجاح!")
else:
    print("الدوال موجودة مسبقاً.")