# عقد التكامل مع Core (Core Integration Contract)

هذا المستند يوثق كيفية تكامل وحدة المستندات (Documents Module) مع النظام المركزي (Core).

## 1. الهوية والصلاحيات (Identity & Permissions)
- **الهوية:** نستخدم `get_current_user` من `app.auth.dependencies` للتحقق من هوية المستخدم من خلال الـ Token (Bearer Token).
- **الصلاحيات (RBAC):** نستخدم `has_permission` من `app.services.authorization` للتحقق من صلاحيات المستخدم.
- **مصدر البيانات:** لا نقبل أي `user_id` أو `role` من الـ Request Body أو الـ Query. يتم استخراجها فقط من الـ Session/Token.
- **التحقق:** يتم التحقق من الصلاحيات قبل تنفيذ أي عملية (رفع، قراءة، حذف، موافقة).

## 2. توجيه البيانات (Data Routing)
- **الوضع الحالي:** نستخدم `get_department_db` (في `app.database`) الذي يقوم بتعيين `search_path` للـ Schema الخاصة بالقسم (مثلاً `hr`).
- **بيانات الاتصال:** لا يتم كشف `DATABASE_URL` أو أي بيانات اتصال في الـ API. يتم استخدام المتغيرات البيئية (Environment Variables) فقط.
- **التحقق من الحالة:** الجلسة تُنشأ ديناميكياً بناءً على `department_id` الذي يمرره المستخدم.

## 3. ملكية المخططات (Schema Ownership)
- **القاعدة المركزية (`public`):** تحتوي على جداول المستخدمين (`users`)، الأقسام (`departments`)، الأدوار (`roles`)، والصلاحيات (`permissions`).
- **قواعد الأقسام (Tenant DBs):** كل قسم له Schema خاصة (مثلاً `hr`). تحتوي على جداول المستندات (`documents`)، المقاطع (`document_chunks`)، الإصدارات (`document_versions`)، سجل التعديلات (`document_revision_logs`)، وسجل التدقيق (`document_audit_logs`).

## 4. عقد المستند (Document Contract)
- **المعرّفات:** `id` (رقم)، `filename` (نص)، `department_id` (نص).
- **الحالات:** `status` (pending_review, approved, archived).
- **الإصدارات:** `version` (رقم) في جدول `documents`، و `version_number` في جدول `document_versions`.
- **بيانات المصدر:** `file_path` (مسار الملف)، `file_type` (نوع الملف)، `extracted_text` (النص المستخرج).
- **أخطاء المعالجة:** يتم تسجيلها في `DocumentAuditLog` و `DocumentRevisionLog`.

## 5. عقد الاسترجاع (Retrieval Contract)
- **المدخلات:** `query` (نص السؤال)، `department_id` (نص).
- **المخرجات:** `answer` (نص الإجابة)، `sources` (قائمة المراجع: اسم المستند، الإصدار، رقم الصفحة/الشريحة، معرف المقطع).
- **الضمانات:**
  - يتم تصفية المحتوى (حسب القسم والصلاحيات) **قبل** التوليد.
  - إذا لم يوجد دليل، يرجع النظام "لا أعرف" (Fail Closed).
  - لا يتم إرجاع النص الخام للمقاطع (`context_used` غير مستخدم في الواجهة).

## 6. عقد الاستهلاك (Usage Contract)
- **الوضع الحالي:** غير مطبق (مؤجل).
- **ملاحظة:** ربط المعالجة بخدمة U1 الداخلية لاحتساب الاستهلاك يتطلب تكامل خارجي.

## 7. واجهة API (API Interface)
- يتم توثيق جميع الطلبات والاستجابات والأخطاء تلقائياً عبر **OpenAPI** (Swagger UI).
- جميع المسارات محمية بالمصادقة (ما عدا `/auth/login`).
- الأخطاء ترجع برسائل آمنة (بدون Stack Traces).

## 8. ملاحظات إضافية
- **المزود (Provider):** الكود يدعم Gemini و OpenAI (يتم الاختيار عبر `LLM_PROVIDER` في `.env`).
- **الأمان:** الملفات المرفوعة يتم فحصها بـ `Magika` (اكتشاف النوع الحقيقي)، ويتم تطبيق حد 20 ميجا.