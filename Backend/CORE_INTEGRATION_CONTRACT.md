# عقد التكامل مع Core (Core Integration Contract)

## 1. الهوية والصلاحيات (Identity & Permissions)
- **الهوية:** تأتي من `get_current_user` (JWT Token). لا تُقبل هوية من الطلب.
- **الصلاحيات:** يتم التحقق عبر `has_permission` من Core (RBAC). يتم التحقق قبل فتح قاعدة بيانات القسم.
- **الـ DTO:** يتم إرجاع بيانات آمنة فقط (`id`, `filename`, `status`, `version`, `upload_date`) ولا يتم إرجاع `file_path` أو `extracted_text` في القوائم.

## 2. توجيه البيانات (Data Routing)
- **الوضع الحالي:** نستخدم `get_department_db` (جلسة مركزية) مع الفلترة الإجبارية بـ `department_id`. لا نقبل `search_path` من الطلب.
- **قواعد الأقسام:** كل قسم سيكون له قاعدة بيانات مستقلة (Tenant DB) في مرحلة لاحقة. حالياً نستخدم Schema `hr` (كتجربة).

## 3. ملكية المخططات (Schema Ownership)
- **القاعدة المركزية:** جداول Core (Users, Departments, Roles).
- **قاعدة القسم:** جداول المستندات (`documents`, `document_chunks`, إلخ).

## 4. عقد المستند (Document Contract)
- **الحالات:** `pending_review`, `approved`, `archived`.
- **الإصدارات:** كل مستند له `version` (رقم)، ويتم أرشفة الإصدار السابق في `document_versions`.

## 5. عقد الاسترجاع (Retrieval Contract)
- **المراجع:** نرجع `sources` تحتوي على (اسم المستند، الإصدار، رقم الصفحة/الشريحة، معرف المقطع).
- **الفلترة:** يتم تصفية المحتوى حسب القسم والصلاحيات **قبل** التوليد.
- **الامتناع:** إذا لم يوجد دليل، يرجع "لا أعرف" (Fail Closed).

## 6. عقد الاستهلاك (Usage Contract)
- **الوضع الحالي:** غير مطبق. يحتاج تكامل مع خدمة U1.

## 7. واجهة API (API Interface)
- **الأخطاء:** ترجع برسائل آمنة (بدون Stack Traces).
- **التنزيل:** الملف الأصلي متاح فقط لرئيس القسم/نائبه/Admin.
- **OpenAPI:** متاح تلقائياً عبر `/docs`.

## 8. التعديلات الأخيرة (Security Updates)
- **رفض هوية Admin:** `runtime_security.py` يرمي `RuntimeError` إذا اكتشف صلاحيات إدارية.
- **تأمين التنزيل:** دالة `download_document` تتحقق من صلاحية `documents:update` (رئيس القسم).
- **إعادة معالجة المقاطع:** دالة `revise_document` تمسح المقاطع القديمة وتعيد تقسيم النص بعد التصحيح.