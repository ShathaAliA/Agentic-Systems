# B1 — Department Documents Schema Design

**التاريخ:** 2026-11-08  
**الفرع:** `documents-ai`  
**المؤلف:** Developer B (Document Management & AI)  
**المراجع:** Developer A (Core Backend)

---

## 1. النطاق

هذه الوثيقة تصف مخطط قاعدة بيانات المستندات لكل قسم (tenant DB).  
المخطط يعيش **داخل قاعدة القسم** (`dept_<32hex>`)، وليس في المركزية.

المركزية تحتفظ فقط بـ:
- `users`, `departments`, `roles`, `permissions`, `role_assignments`
- `department_databases` (registry)

**لا** توجد جداول مستندات في المركزية.

---

## 2. الجداول

### 2.1 `documents`

| العمود | النوع | القيود | الوصف |
|---|---|---|---|
| `id` | `integer` | PK, autoincrement | معرف المستند |
| `filename` | `varchar` | NOT NULL | اسم الملف الأصلي |
| `file_path` | `varchar` | NOT NULL | مسار التخزين (خارج Git) |
| `file_type` | `varchar` | nullable | امتداد الملف (`.pdf`, `.docx`, ...) |
| `extracted_text` | `text` | nullable | النص المستخرج (للنسخة الحالية) |
| `department_id` | `varchar(50)` | nullable | معرف القسم (للتحقق المزدوج) |
| `status` | `varchar(20)` | default `pending_review` | `pending_review` / `approved` / `archived` |
| `version` | `integer` | default `1` | رقم النسخة الحالية |
| `upload_date` | `timestamptz` | default `now()` | تاريخ الرفع |

**الفهارس:**
- `ix_documents_department_id` على `department_id`
- `ix_documents_status` على `status`

**ملاحظات:**
- `file_path` و`extracted_text` **لا يُعادان** في `list_documents` (DTO فقط).
- `status` يتحكم في الظهور في البحث (`ask` يبحث فقط في `approved`).

---

### 2.2 `document_versions`

| العمود | النوع | القيود | الوصف |
|---|---|---|---|
| `id` | `integer` | PK | معرف النسخة |
| `document_id` | `integer` | FK → `documents.id` | المستند |
| `version_number` | `integer` | NOT NULL | رقم النسخة |
| `status` | `varchar(20)` | NOT NULL | `pending` / `approved` / `archived` |
| `created_at` | `timestamptz` | default `now()` | تاريخ الإنشاء |

**القيود:**
- `UNIQUE(document_id, version_number)` — لا تكرار للنسخة.
- `version_number >= 1` (منطقياً).

**الفهارس:**
- `ix_document_versions_document_id` على `document_id`

**ملاحظات:**
- النسخ **immutable** — لا تُعدَّل بعد `approved`. أي تعديل يُنشئ نسخة جديدة.
- `ask` يبحث فقط في النسخ `approved`.
- الإصدار السابق المعتمد **لا يُحذف** عند رفع نسخة جديدة — يُؤرشف فقط.

---

### 2.3 `document_revision_logs`

| العمود | النوع | القيود | الوصف |
|---|---|---|---|
| `id` | `integer` | PK | معرف التصحيح |
| `document_id` | `integer` | FK → `documents.id` | المستند |
| `old_text` | `text` | nullable | النص قبل التصحيح |
| `new_text` | `text` | NOT NULL | النص بعد التصحيح |
| `revised_by` | `varchar(36)` | NOT NULL | معرف المستخدم (من Central) |
| `revised_at` | `timestamptz` | default `now()` | وقت التصحيح |

**الفهارس:**
- `ix_document_revision_logs_document_id` على `document_id`

**ملاحظات:**
- هذا سجل **immutable** (append-only). لا تحديث/حذف.
- يُستخدم للتدقيق وللرجوع إلى النص السابق.

---

### 2.4 `document_audit_logs`

| العمود | النوع | القيود | الوصف |
|---|---|---|---|
| `id` | `integer` | PK | معرف الحدث |
| `document_id` | `integer` | FK → `documents.id` | المستند |
| `action` | `varchar(50)` | NOT NULL | `upload` / `approve` / `archive` / `revise` |
| `performed_by` | `varchar(36)` | NOT NULL | معرف المستخدم (من Central) |
| `timestamp` | `timestamptz` | default `now()` | وقت الحدث |

**الفهارس:**
- `ix_document_audit_logs_document_id` على `document_id`

**ملاحظات:**
- **immutable** (append-only). لا تحديث/حذف.
- هذا هو سجل التدقيق الرسمي (مطلوب من prompt: "Audit sensitive document actions").

---

### 2.5 `document_chunks`

| العمود | النوع | القيود | الوصف |
|---|---|---|---|
| `id` | `integer` | PK | معرف المقطع |
| `document_id` | `integer` | FK → `documents.id` | المستند |
| `content` | `text` | NOT NULL | نص المقطع |
| `page_number` | `integer` | nullable | رقم الصفحة/الشريحة/الورقة |
| `embedding` | `vector(768)` | nullable | تضمين دلالي (pgvector) |

**الفهارس:**
- `ix_document_chunks_document_id` على `document_id`
- `ix_document_chunks_embedding_hnsw` — HNSW cosine على `embedding`

**ملاحظات:**
- `page_number` يُملأ من `file_processor` (مستقبلاً — P1).
- HNSW يسمح بالبحث الدلالي السريع.
- `embedding` nullable — لأن بعض الملفات قد لا تحتاج embedding (نص قصير).

---

## 3. العلاقات مع المركزية

| كيان مركزي | كيف يُستخدم في tenant |
|---|---|
| `users.id` | يُخزَّن في `revised_by`, `performed_by` (varchar(36)) |
| `departments.id` | يُخزَّن في `documents.department_id` (varchar(50)) |
| `role_assignments` | تُستخدم في `has_permission` (عبر `get_central_db`) |

**لا Foreign Key بين tenant و central** — لأن قواعد البيانات منفصلة. التحقق يتم عبر `authorized_department_session` (Central → Department).

---

## 4. عزل الأقسام

**الآلية:**
1. المستخدم مصادق عليه في المركزية (`get_current_user`).
2. `has_permission(central_db, user_id, perm, department_id)` يتحقق من RBAC.
3. `authorized_department_session(user_id, department_id, perm)`:
   - يتحقق من `department_databases.status='active'`.
   - يتحقق من `departments.archived_at IS NULL`.
   - يفتح جلسة على **قاعدة القسم فقط**.
4. `router.py` يفلتر صراحةً بـ `department_id` في كل استعلام (defense in depth).

**النتيجة:** مستخدم HR لا يستطيع الوصول إلى Finance، حتى لو غيّر `department_id` في الـ URL — لأن `authorized_department_session` سترفض.

---

## 5. Migration Contract

- **Revision:** `tenant_0002` (مثبت، ليس `head`).
- **required_tables:** `documents`, `document_versions`, `document_revision_logs`, `document_audit_logs`, `document_chunks`, `_provisioning_identity`.
- **ready:** `true` بعد اجتياز verify.
- **contract_version:** `1.0`.

**تفاصيل:** انظر `tenant-contract.json`.

---

## 6. Runtime Privileges (مقترحة)

| الجدول | SELECT | INSERT | UPDATE | DELETE |
|---|---|---|---|---|
| `documents` | ✅ | ✅ | ✅ | ❌ (أرشفة فقط) |
| `document_versions` | ✅ | ✅ | ✅ | ❌ |
| `document_revision_logs` | ✅ | ✅ | ❌ | ❌ (immutable) |
| `document_audit_logs` | ✅ | ✅ | ❌ | ❌ (immutable) |
| `document_chunks` | ✅ | ✅ | ✅ | ✅ (إعادة فهرسة) |
| `_provisioning_identity` | ✅ | ❌ | ❌ | ❌ |

**⚠️ ملاحظة:** `TenantMigrator` الحالي لا يقرأ `table_privileges` من العقد. هذا يحتاج تعديلاً في `tenant_migrations.py` (Developer A) أو في `worker.py`. **مذكور في التقرير لشذى.**

---

## 7. القرارات المعلّقة (تحتاج موافقة Developer A)

1. **`table_privileges` في العقد:** `TenantMigrator` لا يقرأها. هل نعدّل `tenant_migrations.py`؟
2. **`verify_contract`:** هل يجب أن يتحقق من الفهارس (`HNSW`) أيضاً، أم فقط الجداول؟
3. **Legal activation:** بعد الـ push، هل أفعّل Legal مباشرة، أم ننتظر review؟

---

## 8. ما لم يُنجز بعد (P1)

- `document_audiences` (جمهور المستندات).
- `location` كامل (صفحة/شريحة/ورقة) — يُخزَّن حالياً في `page_number` فقط.
- تكامل U1 (استهلاك).
- OCR داخل PPTX.

---

## التوقيع
- **Developer B:** ____________________
- **Developer A (review):** ____________________