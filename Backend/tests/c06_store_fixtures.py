from uuid import uuid4

DEPARTMENT = "22222222-2222-4222-8222-222222222222"


def answer(request_id=None, *, status="answered"):
    return dict(question_request_id=request_id or str(uuid4()),
        conversation_id="11111111-1111-4111-8111-111111111111", status=status,
        answer="مدة الحفظ أربعة عشر يومًا.", answer_kind="verbatim_excerpt", llm_called=False,
        sources=[dict(department_id=DEPARTMENT, document_id=7, version_id="v1", version_number=1,
                      name="سياسة اصطناعية", quote="مدة الحفظ أربعة عشر يومًا.",
                      location=dict(kind="pdf_page", page_number=1))],
        unanswered_departments=[], handoff_available=False)
