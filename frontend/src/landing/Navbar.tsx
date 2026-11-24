import { useEffect, useRef, useState } from 'react'
import ThemeToggle from '../components/ThemeToggle'
import { DEPARTMENTS } from './departments'
import './Navbar.css'

const LINKS = [
  { id: 'problem', href: '#problem', label: 'ما نحلّه' },
  { id: 'platform', href: '#platform', label: 'ما نقدّمه' },
  { id: 'security', href: '#security', label: 'الأمان والتدقيق' },
  { id: 'faq', href: '#faq', label: 'الأسئلة الشائعة' },
]

export default function Navbar() {
  const [stuck, setStuck] = useState(false)
  const [mega, setMega] = useState(false)
  const [menu, setMenu] = useState(false)
  const [active, setActive] = useState(0)
  const [current, setCurrent] = useState('')
  const root = useRef<HTMLElement>(null)
  const timer = useRef<number>(0)

  useEffect(() => {
    const onScroll = () => setStuck(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setMega(false); setMenu(false) }
    }
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) { setMega(false); setMenu(false) }
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [])

  useEffect(() => {
    if (!('IntersectionObserver' in window)) return
    const ids = LINKS.map((l) => l.id)
    const els = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => Boolean(el))
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) setCurrent(e.target.id) }),
      { rootMargin: '-35% 0px -60% 0px' },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [])

  const hold = () => { window.clearTimeout(timer.current); setMega(true) }
  const release = () => { timer.current = window.setTimeout(() => setMega(false), 140) }
  const close = () => { setMega(false); setMenu(false) }
  const dept = DEPARTMENTS[Math.min(active, DEPARTMENTS.length - 1)]

  return (
    <header className={`nb${stuck ? ' nb--stuck' : ''}`} ref={root}>
      <div className="nb__bar">
        <nav className="nb__links" aria-label="التنقل الرئيسي">
          <div className="nb__mega-wrap" onMouseEnter={hold} onMouseLeave={release}>
            <button
              type="button"
              className="nb__link"
              aria-expanded={mega}
              aria-controls="mega"
              aria-current={mega ? 'true' : undefined}
              onClick={(e) => (e.detail === 0 ? setMega((v) => !v) : setMega(true))}
            >
              الأقسام
              <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" className="nb__caret">
                <path d="M2 4.2 6 8l4-3.8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
          {LINKS.map((l) => (
            <a key={l.href} className="nb__link" href={l.href} aria-current={current === l.id ? 'true' : undefined} onClick={close}>{l.label}</a>
          ))}
        </nav>

        <div className="nb__end">
          <ThemeToggle />
          <button
            type="button"
            className="nb__burger"
            aria-label="القائمة"
            aria-expanded={menu}
            aria-controls="mobile-menu"
            onClick={() => setMenu((v) => !v)}
          >
            <span /><span /><span />
          </button>
        </div>
      </div>

      <div id="mega" className="nb__mega" hidden={!mega} onMouseEnter={hold} onMouseLeave={release}>
        <ul className="nb__list">
          {DEPARTMENTS.map((d, i) => (
            <li key={d.id}>
              <span
                className="nb__item"
                aria-current={i === active ? 'true' : undefined}
                onMouseEnter={() => setActive(i)}
              >
                {d.name}
              </span>
            </li>
          ))}
        </ul>
        <div className="nb__preview" aria-live="polite">
          <div className="pv">
            <div className="pv__head">
              <span className="pv__title">أسئلة يمكنك طرحها على {dept.name}</span>
            </div>
            <ul className="pv__rows">
              {dept.samples.map((r) => (
                <li key={r}><span className="pv__dot" />{r}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      <div id="mobile-menu" className="nb__mobile" hidden={!menu}>
        <span className="nb__mobile-h">الأقسام</span>
        {DEPARTMENTS.map((d) => (
          <span key={d.id} className="nb__mobile-dept">{d.name}</span>
        ))}
        <span className="nb__mobile-h">الصفحة</span>
        {LINKS.map((l) => (
          <a key={l.href} href={l.href} aria-current={current === l.id ? 'true' : undefined} onClick={close}>{l.label}</a>
        ))}
      </div>
    </header>
  )
}
