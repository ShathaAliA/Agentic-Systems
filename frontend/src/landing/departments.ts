/**
 * Departments shown in the landing page's navbar and footer.
 * The landing page is public, so it cannot ask the backend which departments exist.
 * These are default placeholders: replace them with the company's real department names.
 */
export interface DepartmentLink {
  id: string
  name: string
  /** Sample questions shown in the navbar preview: illustrations, not real documents. */
  samples: string[]
}

export const DEPARTMENTS: DepartmentLink[] = [
  { id: 'it', name: 'تقنية المعلومات', samples: ['كيف أطلب صلاحية دخول إلى نظام جديد؟', 'ما إجراء الإبلاغ عن جهاز مفقود؟', 'كم مدة تفعيل الوصول عن بُعد؟'] },
  { id: 'hr', name: 'الموارد البشرية', samples: ['كم يوم إجازة سنوية أستحق؟', 'كيف أقدّم طلب إجازة مرضية؟', 'ما سياسة العمل عن بُعد؟'] },
  { id: 'finance', name: 'المالية', samples: ['ما الحد الأعلى لمصروف السفر اليومي؟', 'كيف أصرف سلفة مالية؟', 'متى يُصرف تعويض المصروفات؟'] },
  { id: 'investment', name: 'الاستثمار', samples: ['ما معايير تقييم الفرصة الاستثمارية؟', 'من يعتمد قرارات الاستثمار؟', 'ما الحد الأعلى للتركّز في أصل واحد؟'] },
  { id: 'procurement', name: 'المشتريات', samples: ['متى يلزم عرض أسعار ثالث؟', 'كيف أضيف مورّدًا جديدًا؟', 'ما مدة اعتماد أمر الشراء؟'] },
  { id: 'legal', name: 'الشؤون القانونية', samples: ['من يراجع العقود قبل التوقيع؟', 'ما شروط تضارب المصالح؟', 'كيف أحفظ مستندًا خاضعًا للسرية؟'] },
  { id: 'marketing', name: 'التسويق', samples: ['ما دليل استخدام الهوية البصرية؟', 'كيف أطلب اعتماد حملة؟', 'ما مدة مراجعة المحتوى؟'] },
  { id: 'operations', name: 'العمليات', samples: ['ما إجراءات الإبلاغ عن حادث؟', 'كيف أطلب صيانة عاجلة؟', 'ما جدول المراجعة الدورية؟'] },
]
