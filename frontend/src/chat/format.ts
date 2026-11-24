import type { SourceLocation } from '../api/chat'
import { ApiError } from '../api/client'

const number = new Intl.NumberFormat('ar-EG')
const when = new Intl.DateTimeFormat('ar-EG', { day: 'numeric', month: 'long', hour: 'numeric', minute: '2-digit' })

export const fmtNumber = (n: number) => number.format(n)
export const fmtWhen = (iso: string) => when.format(new Date(iso))

export function locationLabel(l: SourceLocation): string {
  switch (l.kind) {
    case 'pdf_page': return `ص ${fmtNumber(l.page_number)}`
    case 'slide': return `شريحة ${fmtNumber(l.slide_number)}`
    case 'sheet_range': return `${l.sheet_name} · ${l.cell_range}`
    case 'image': return `صورة ${fmtNumber(l.image_number)}`
    default: return l.section
  }
}

/** One plain-language message per failure; codes come from the backend's `detail` field. */
export function askErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return 'تعذّر إرسال السؤال. أعد المحاولة.'
  switch (error.code) {
    case 'network': return 'تعذّر الاتصال بالخدمة. تحقق من الاتصال وأعد المحاولة.'
    case 'document_result_integration_unavailable': return 'خدمة الأسئلة غير مفعّلة حاليًا في هذه البيئة.'
    case 'question_processing_disabled':
    case 'usage_policy_not_configured': return 'الأسئلة متوقفة مؤقتًا. تواصل مع مسؤول النظام.'
    case 'company_budget_limit': return 'بلغت الشركة حدّ ميزانية الأسئلة لهذا الشهر.'
    case 'question_technical_failure_refunded': return 'تعذّرت معالجة السؤال ولم يُخصم من رصيدك. أعد المحاولة.'
    case 'question_processing_or_uncertain': return 'سؤالك ما زال قيد المعالجة. لا ترسله مجددًا، وحدّث الصفحة بعد قليل.'
  }
  if (error.status === 403) return 'لا تملك صلاحية السؤال في هذا القسم.'
  if (error.status === 429) return 'بلغت حدّ الأسئلة المسموح. أعد المحاولة لاحقًا.'
  return 'تعذّر إرسال السؤال. أعد المحاولة.'
}

export function handoffErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return 'تعذّر إرسال الطلب. أعد المحاولة.'
  switch (error.code) {
    case 'network': return 'تعذّر الاتصال بالخدمة. تحقق من الاتصال وأعد المحاولة.'
    case 'handoff_already_exists': return 'سبق أن أرسلت هذا السؤال إلى القسم. افتح صندوق الوارد لمتابعته.'
    case 'handoff_open_limit': return 'لديك طلبات مفتوحة كثيرة. انتظر حتى تُغلق بعضها.'
    case 'handoff_credit_exhausted': return 'انتهى رصيدك، ويمكن تحويل آخر سؤال في المحادثة فقط.'
    case 'handoff_request_forbidden': return 'لا تملك صلاحية إرسال طلب إلى هذا القسم.'
    case 'handoff_creation_conflict': return 'تغيّر الطلب. حدّث الصفحة ثم أعد المحاولة.'
  }
  return 'تعذّر إرسال الطلب. أعد المحاولة.'
}
