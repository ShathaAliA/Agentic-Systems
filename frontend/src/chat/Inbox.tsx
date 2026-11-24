import { useCallback, useEffect, useRef, useState } from 'react'
import { getNotifications, getUnreadCount, type Notification, type NotificationKind } from '../api/handoffs'
import { fmtNumber } from './format'

const POLL_MS = 60_000

const LABEL: Record<NotificationKind, (dept: string) => string> = {
  reply: (d) => `ردّ عليك ${d}`,
  new_request: (d) => `طلب جديد في ${d}`,
  claimed: (d) => `استلم ${d} طلبك`,
  transferred: (d) => `حُوِّل طلبك داخل ${d}`,
  closed: (d) => `أغلق ${d} طلبك`,
  reopened: (d) => `أُعيد فتح طلبك في ${d}`,
}

function ago(iso: string) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 1) return 'الآن'
  if (mins < 60) return `قبل ${fmtNumber(mins)} د`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `قبل ${fmtNumber(hours)} س`
  return new Date(iso).toLocaleDateString('ar')
}

interface Props {
  departmentName: (id: string) => string
  onOpen: (handoffId: string) => void
  /** Bumped by the parent after reading a handoff so the badge refreshes at once. */
  refreshKey: number
}

export default function Inbox({ departmentName, onOpen, refreshKey }: Props) {
  const [count, setCount] = useState(0)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Notification[] | null>(null)
  const [failed, setFailed] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  const refreshCount = useCallback(() => {
    getUnreadCount().then((r) => setCount(r.unread_handoffs)).catch(() => undefined)
  }, [])

  useEffect(() => {
    refreshCount()
    const id = window.setInterval(() => { if (!document.hidden) refreshCount() }, POLL_MS)
    return () => window.clearInterval(id)
  }, [refreshCount, refreshKey])

  useEffect(() => {
    if (!open) return
    setFailed(false)
    getNotifications()
      .then((r) => { setItems(r.items); setCount(r.unread_handoffs) })
      .catch(() => { setItems([]); setFailed(true) })
    const onDown = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, refreshKey])

  return (
    <div className="inbox" ref={root}>
      <button
        type="button"
        className="inbox__btn"
        aria-label={count > 0 ? `صندوق الوارد، ${fmtNumber(count)} غير مقروء` : 'صندوق الوارد'}
        aria-expanded={open}
        aria-controls="inbox-panel"
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M3 13.5 5.6 5.8A2 2 0 0 1 7.5 4.5h9a2 2 0 0 1 1.9 1.3L21 13.5V18a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z M3 13.5h5.2a1 1 0 0 1 1 .8 2.9 2.9 0 0 0 5.6 0 1 1 0 0 1 1-.8H21" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
        {count > 0 && <span className="inbox__badge" aria-hidden="true">{count > 9 ? '٩+' : fmtNumber(count)}</span>}
      </button>

      {open && (
        <div id="inbox-panel" className="inbox__panel" role="region" aria-label="الإشعارات">
          <h2 className="inbox__title">صندوق الوارد</h2>
          {items === null && <p className="inbox__note" role="status">جارٍ التحميل…</p>}
          {failed && <p className="inbox__note" role="alert">تعذّر تحميل الإشعارات. حاول لاحقًا.</p>}
          {items !== null && !failed && items.length === 0 && <p className="inbox__note">لا توجد إشعارات جديدة.</p>}
          {items !== null && items.length > 0 && (
            <ul className="inbox__list">
              {items.map((n) => (
                <li key={n.handoff_id}>
                  <button
                    type="button"
                    className="inbox__item"
                    onClick={() => { setOpen(false); onOpen(n.handoff_id) }}
                  >
                    <span className="inbox__dot" aria-hidden="true" />
                    <span className="inbox__text">
                      <span className="inbox__line">{LABEL[n.kind](departmentName(n.department_id))}</span>
                      <span className="inbox__time">
                        {n.unread_messages > 0 && `${fmtNumber(n.unread_messages)} ${n.unread_messages === 1 ? 'رسالة جديدة' : 'رسائل جديدة'} · `}
                        {ago(n.activity_at)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
