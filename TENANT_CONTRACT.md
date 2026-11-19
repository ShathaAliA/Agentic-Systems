# عقد الـ Tenant (Tenant Contract)

## 1. المعلومات الأساسية
- **Schema Name:** `hr`
- **Required Revision:** `tenant_0001_documents_tables`
- **Required Tables:**
  - `documents`
  - `document_chunks`
  - `document_audit_logs`

## 2. دليل جاهزية المخطط (Schema Readiness Guide)
- يجب أن يحتوي `schema hr` على الجداول الثلاثة المذكورة أعلاه.
- يجب أن يكون عمود `embedding` في `document_chunks` من نوع `vector(768)`.
- يجب أن يكون هناك فهرس `HNSW` على `embedding` باستخدام `vector_cosine_ops`.
- يجب أن تكون إضافة `pgvector` مفعلة في `schema hr` (أو `public` مع `search_path`).

## 3. توحيد نوع معرّف القسم
- `department_id` في جدول `documents` هو `String(50)` (نصي)، ليتوافق مع `Core`.

## 4. ملاحظات التكامل
- يتم طلب جلسة قاعدة القسم عبر `get_department_db`، الذي يحدد `search_path` للـ Schema المطلوبة.
- لا يتم قبول بيانات اتصال من الطلب (يتم استخدام `DATABASE_URL` من `.env`).
- صلاحيات المستخدم (RBAC) يتم التحقق منها عبر `has_permission` من `Core`.