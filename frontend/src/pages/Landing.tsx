import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import Navbar from '../landing/Navbar'
import { Faq, Footer, Problem, Security, Solution } from '../landing/Sections'
import './Landing.css'

const GradientBackground = lazy(() => import('../components/GradientBackground'))

const DRAFT_KEY = 'ai-platform.draft'

/** The question box: a glowing bronze edge circles it. Sending carries the question to the sign-in. */
function AskBox() {
  const navigate = useNavigate()
  const [text, setText] = useState('')

  function submit(e: FormEvent) {
    e.preventDefault()
    const q = text.trim()
    if (!q) return
    try { sessionStorage.setItem(DRAFT_KEY, q) } catch { /* storage blocked: the question is simply not carried over */ }
    navigate('/login')
  }

  return (
    <form className="ask" onSubmit={submit}>
      <div className="ask__box">
        <label className="visually-hidden" htmlFor="hero-question">سؤالك</label>
        <textarea
          id="hero-question"
          className="ask__input"
          rows={1}
          maxLength={4000}
          placeholder="اكتب سؤالك عن مستندات قسمك…"
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = `${Math.min(e.target.scrollHeight, 132)}px`
            e.target.style.overflowY = e.target.scrollHeight > 132 ? 'auto' : 'hidden'
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) e.currentTarget.form?.requestSubmit()
          }}
        />
        <button className="ask__send" type="submit" disabled={!text.trim()} aria-label="إرسال السؤال">
          <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
            <path d="M16 10H4M9 5l-5 5 5 5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <p className="ask__hint">سجّل دخولك لتصلك الإجابة</p>
    </form>
  )
}

const EXAMPLES = [
  {
    dept: 'الموارد البشرية',
    q: 'كم يوم إجازة سنوية يحق لي بعد خمس سنوات خدمة؟',
    a: '٣٠ يوم عمل في السنة.',
    rest: ' يزداد الاستحقاق من ٢١ إلى ٣٠ يومًا بعد خمس سنوات خدمة متصلة.',
    src: 'سياسة الإجازة السنوية · ص ٣',
    credits: '٤٢ من ٥٠',
  },
  {
    dept: 'المالية',
    q: 'ما الحد الأعلى لمصروف السفر اليومي داخل المملكة؟',
    a: '٤٥٠ ريالًا في اليوم.',
    rest: ' تشمل الإقامة والوجبات، وما زاد عليها يحتاج موافقة المدير المباشر.',
    src: 'دليل مصروفات السفر · ص ٧',
    credits: '٣٨ من ٥٠',
  },
  {
    dept: 'تقنية المعلومات',
    q: 'كيف أطلب صلاحية الدخول إلى الشبكة من خارج المكتب؟',
    a: 'من بوابة الخدمات.',
    rest: ' اختر «طلب وصول عن بُعد»، وتُفعَّل الصلاحية خلال يوم عمل بعد موافقة مديرك.',
    src: 'إجراءات الوصول عن بُعد · ص ٢',
    credits: '٤٦ من ٥٠',
  },
]

/** Three sample dispatches pile up as you scroll: each one pins, then shrinks and leans back as the next slides over it. */
function Example() {
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = root.current
    if (!el || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const items = Array.from(el.querySelectorAll<HTMLElement>('.stack__item'))
    let raf = 0

    const update = () => {
      raf = 0
      const vh = window.innerHeight
      items.forEach((item, i) => {
        const card = item.firstElementChild as HTMLElement
        const stick = parseFloat(getComputedStyle(item).top) || 0
        // arrival: the card grows to full size as it climbs into place
        const arrive = i === 0 ? 1 : Math.min(1, Math.max(0, 1 - (item.getBoundingClientRect().top - stick) / (vh * 0.55)))
        // burial: every later card that slides over adds to how far this one recedes
        let buried = 0
        for (let j = i + 1; j < items.length; j++) {
          const next = items[j]
          const d = next.getBoundingClientRect().top - parseFloat(getComputedStyle(next).top)
          buried += Math.min(1, Math.max(0, 1 - d / (card.offsetHeight * 0.9)))
        }
        const scale = (0.93 + 0.07 * arrive) - 0.045 * buried
        card.style.transform = `perspective(1100px) translateY(${(-16 * buried).toFixed(1)}px) rotateX(${(5 * buried + 3 * (1 - arrive)).toFixed(2)}deg) scale(${scale.toFixed(4)})`
        card.style.opacity = String(0.55 + 0.45 * arrive)
        card.style.filter = buried > 0.01 ? `brightness(${(1 - 0.22 * buried).toFixed(3)})` : ''
      })
    }
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update) }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  return (
    <section className="sec sec--narrow" id="example" aria-labelledby="example-title">
      <h2 className="sec__title sec__title--center" id="example-title">هكذا تصلك الإجابة</h2>
      <div className="stack" ref={root}>
        {EXAMPLES.map((x, i) => (
          <div className="stack__item" key={x.dept} style={{ top: `${96 + i * 18}px` }}>
            <article className="proof" aria-label={`مثال توضيحي ${i + 1} من ${EXAMPLES.length}`}>
              <div className="proof__head">
                <span className="chip chip--dept">{x.dept}</span>
                <span className="chip chip--demo">مثال توضيحي · {['١', '٢', '٣'][i]} من ٣</span>
                <span className="proof__credits">الرصيد <strong>{x.credits.split(' ')[0]}</strong> {x.credits.slice(x.credits.indexOf(' ') + 1)} سؤالًا</span>
              </div>
              <p className="proof__question">{x.q}</p>
              <p className="proof__answer"><strong>{x.a}</strong>{x.rest}</p>
              <span className="chip chip--source">المصدر: {x.src}</span>
            </article>
          </div>
        ))}
      </div>
    </section>
  )
}

/** Text eases in as it scrolls into view. Elements are tagged here, so without script (or with reduced motion) nothing is hidden. */
function useReveal() {
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return
    const targets = Array.from(document.querySelectorAll<HTMLElement>(
      '.hero__title, .hero__sub, .ask, .sec__title, .sec__lead, .rows__item, .bento__big, .bento__small, .specs__row, .faq__item, .foot__brand, .foot__col',
    ))
    const seen = new Map<Element, number>()
    targets.forEach((t) => {
      const sibs = Array.from(t.parentElement?.children ?? [])
      t.style.setProperty('--d', `${Math.min(sibs.indexOf(t), 5) * 90}ms`)
      t.classList.add('reveal')
    })
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return
        const t = e.target as HTMLElement
        t.classList.add('in')
        io.unobserve(t)
        // once settled, drop the helper classes so they never interfere with hover or focus styles
        seen.set(t, window.setTimeout(() => t.classList.remove('reveal', 'in'), 1400))
      })
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 })
    targets.forEach((t) => io.observe(t))
    return () => {
      io.disconnect()
      seen.forEach((id) => window.clearTimeout(id))
      targets.forEach((t) => t.classList.remove('reveal', 'in'))
    }
  }, [])
}

export default function Landing() {
  useReveal()
  return (
    <div className="page" id="top">
      <Navbar />
      <main>
        <div className="hero world-bg">
          <Suspense fallback={null}>
            <GradientBackground />
          </Suspense>

          <div className="hero__main">
            <h1 className="hero__title">اسأل مستندات قسمك فقط</h1>
            <p className="hero__sub">
              اكتب سؤالك، وتصلك إجابة من مستندات قسمك المعتمدة مع مصدرها.
            </p>
            <AskBox />
          </div>
        </div>

        <Example />
        <Problem />
        <Solution />
        <Security />
        <Faq />
      </main>
      <Footer />
    </div>
  )
}
