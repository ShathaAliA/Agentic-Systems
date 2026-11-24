import { ApiError } from '../api/client'

/** Plain-language message for an administration failure; codes come from the backend's `detail` field. */
export function adminError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'تعذّر تنفيذ العملية. أعد المحاولة.'
  switch (error.code) {
    case 'network': return 'تعذّر الاتصال بالخدمة. تحقق من الاتصال وأعد المحاولة.'
    case 'user_administration_denied': return 'لا تملك صلاحية إدارة المستخدمين.'
    case 'identity_or_assignment_conflict': return 'يوجد مستخدم بهذا البريد الإلكتروني أو تعارض في البيانات.'
    case 'department_or_head_conflict': return 'يوجد قسم بهذا الاسم أو تعارض في رئيس القسم.'
  }
  if (error.status === 403) return 'لا تملك صلاحية تنفيذ هذه العملية.'
  if (error.status === 404) return 'العنصر غير موجود.'
  if (error.status === 409) return 'تعذّر تنفيذ العملية بسبب تعارض مع حالة حالية.'
  if (error.status === 422) return 'البيانات المدخلة غير صالحة.'
  return 'تعذّر تنفيذ العملية. أعد المحاولة.'
}

export const USER_STATUS: Record<string, string> = { active: 'نشط', inactive: 'معطّل' }
export const DEPT_STATUS: Record<string, string> = {
  provisioning: 'قيد التجهيز',
  active: 'نشط',
  failed: 'فشل التجهيز',
  archived: 'مؤرشف',
}
