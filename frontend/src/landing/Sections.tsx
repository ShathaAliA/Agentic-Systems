import { DEPARTMENTS } from './departments'
import './Sections.css'

const PROBLEMS = [
  { t: 'المعلومة موجودة، لكن الوصول إليها بطيء', d: 'يبحث الموظف يدويًا في ملفات القسم ليجد بندًا واحدًا في سياسة أو نموذج.' },
  { t: 'الإجابة العامة لا تذكر مصدرها', d: 'لا يعرف الموظف من أين جاءت المعلومة، فلا يستطيع أن يثق بها أو يراجعها.' },
  { t: 'لا أحد يعرف من سأل عن ماذا', d: 'دون سجل ثابت يصعب تدقيق الاستخدام أو ضبط تكلفته.' },
]

export function Problem() {
  return (
    <section className="sec" id="problem" aria-labelledby="problem-title">
      <h2 id="problem-title" className="sec__title sec__title--center">ما الذي تحلّه المنصة؟</h2>
      <ul className="rows">
        {PROBLEMS.map((p) => (
          <li key={p.t} className="rows__item">
            <h3 className="rows__title">{p.t}</h3>
            <p className="rows__text">{p.d}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function Solution() {
  return (
    <section className="sec" id="platform" aria-labelledby="solution-title">
      <h2 id="solution-title" className="sec__title sec__title--center">ما تقدّمه لكل قسم</h2>
      <div className="bento">
        <article className="bento__big">
          <h3 className="bento__title">إجابة من مستندات قسمك، مع مصدرها</h3>
          <p className="bento__text">لا تُعرض إجابة دون اقتباس من المستند الذي استندت إليه، فتتحقق منه بنفسك.</p>
          <blockquote className="quote">
            <span className="quote__src">سياسة الإجازة السنوية · ص ٣</span>
            «تُمنح الإجازة السنوية ثلاثين يومًا لمن أتم خمس سنوات خدمة.»
            <span className="quote__cap">اقتباس توضيحي</span>
          </blockquote>
        </article>
        <article className="bento__small">
          <h3 className="bento__title">رصيد أسئلة تحت السيطرة</h3>
          <p className="bento__text">حدّ يومي وشهري لكل موظف وميزانية للشركة، ويظهر رصيدك قبل أن تسأل.</p>
        </article>
      </div>
    </section>
  )
}

const SPECS = [
  ['جلسات تنتهي', 'تنتهي الجلسة تلقائيًا ولا تُجدَّد دون دخول جديد.'],
  ['محاولات دخول محدودة', 'يُحدّ عدد المحاولات لكل حساب ولكل عنوان.'],
  ['مستندات معتمدة فقط', 'لا يُجاب من مستند قبل اعتماده وتحديد من يراه.'],
  ['سجل لا يُعدَّل', 'تُحفظ محادثاتك وردودها في سجل ثابت وخاص بك.'],
  ['حدّ للتكلفة', 'حدود لكل موظف وميزانية شهرية للشركة.'],
]

export function Security() {
  return (
    <section className="sec sec--split" id="security" aria-labelledby="security-title">
      <div className="sec__text">
        <h2 id="security-title" className="sec__title">الأمان والتدقيق</h2>
        <p className="sec__lead">
          هذه هي الضوابط المطبّقة في المنصة اليوم. نسخة الدخول الحالية تجريبية، ودخول Microsoft Entra قيد الإعداد.
        </p>
      </div>
      <dl className="specs">
        {SPECS.map(([t, d]) => (
          <div key={t} className="specs__row">
            <dt>{t}</dt>
            <dd>{d}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

const FAQ = [
  ['من أي مستندات تأتي الإجابات؟', 'من مستندات قسمك المعتمدة فقط، وتُعرض مع كل إجابة مصدرها لتتحقق منها بنفسك.'],
  ['ماذا يحدث إذا انتهى رصيد أسئلتي؟', 'يُقفل صندوق السؤال مع رسالة توضح السبب. الحدّ يومي وشهري ويُضبط بسياسة الشركة، وللتفاصيل تواصل مع مسؤول النظام.'],
  ['هل تُحفظ أسئلتي؟', 'نعم، في محادثات خاصة بك. سجل المحادثات ثابت ولا يُعدَّل.'],
  ['ماذا لو لم توجد إجابة في المستندات؟', 'تعرض المنصة ذلك صراحة دون ذكر مصادر، ولا تخمّن إجابة.'],
  ['هل تسجيل الدخول الحالي نهائي؟', 'لا. هو تسجيل تجريبي لبيئة التطوير، وتسجيل الدخول عبر Microsoft Entra قيد الإعداد.'],
]

export function Faq() {
  return (
    <section className="sec sec--narrow" id="faq" aria-labelledby="faq-title">
      <h2 id="faq-title" className="sec__title sec__title--center">الأسئلة الشائعة</h2>
      <div className="faq">
        {FAQ.map(([q, a]) => (
          <details key={q} className="faq__item">
            <summary>{q}</summary>
            <p>{a}</p>
          </details>
        ))}
      </div>
    </section>
  )
}

export function Footer() {
  return (
    <footer className="foot">
      <div className="foot__grid">
        <div className="foot__brand">
          <span className="foot__name">منصة الذكاء الاصطناعي</span>
          <p>منصة داخلية تجيب موظفي الشركة من مستندات قسمهم المعتمدة فقط، مع مصدر لكل إجابة.</p>
        </div>
        <div className="foot__col">
          <h2 className="foot__h">الأقسام</h2>
          {DEPARTMENTS.map((d) => (
            <span key={d.id}>{d.name}</span>
          ))}
        </div>
        <nav className="foot__col" aria-label="أقسام الصفحة">
          <h2 className="foot__h">الصفحة</h2>
          <a href="#problem">ما نحلّه</a>
          <a href="#platform">ما تقدّمه لكل قسم</a>
          <a href="#security">الأمان والتدقيق</a>
          <a href="#faq">الأسئلة الشائعة</a>
        </nav>
        <div className="foot__col">
          <h2 className="foot__h">ملاحظات</h2>
          <span>نسخة تجريبية للتطوير</span>
          <span>دخول Microsoft Entra قيد الإعداد</span>
        </div>
      </div>
      <div className="foot__bar">
        <span>© ٢٠٢٦ منصة الذكاء الاصطناعي</span>
        <a href="#top">العودة للأعلى</a>
      </div>
    </footer>
  )
}
