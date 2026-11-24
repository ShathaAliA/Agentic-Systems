import { useEffect, useRef, useState } from 'react'
import {
  getHandoff, listHandoffMessages, markHandoffRead, postHandoffMessage,
  type Handoff, type HandoffMessage,
} from '../api/handoffs'

interface Props {
  handoffId: string
  departmentName: string
  onRead: () => void
  onBack: () => void
}

/** The human conversation with the department: their replies and the employee's own messages. */
export default function HandoffThread({ handoffId, departmentName, onRead, onBack }: Props) {
  const [handoff, setHandoff] = useState<Handoff | null>(null)
  const [messages, setMessages] = useState<HandoffMessage[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    setHandoff(null); setMessages(null); setFailed(false)
    Promise.all([getHandoff(handoffId), listHandoffMessages(handoffId)])
      .then(([h, m]) => {
        if (!alive) return
        setHandoff(h)
        setMessages(m.items)
        const last = m.items.length ? m.items[m.items.length - 1].sequence : null
        markHandoffRead(handoffId, last).then(onRead).catch(() => undefined)
      })
      .catch(() => alive && setFailed(true))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handoffId])

  useEffect(() => { end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }) }, [messages])

  async function send() {
    const body = draft.trim()
    if (!body || sending) return
    setSending(true); setSendError(false)
    try {
      await postHandoffMessage(handoffId, body)
      setDraft('')
      const m = await listHandoffMessages(handoffId)
      setMessages(m.items)
    } catch {
      setSendError(true)
    } finally {
      setSending(false)
    }
  }

  const closed = handoff?.status === 'closed'

  return (
    <>
      <div className="hand">
        <div className="hand__head">
          <button type="button" className="link-btn" onClick={onBack}>العودة للمحادثات</button>
          <h2 className="hand__title">محادثة مع {departmentName}</h2>
          {handoff && <p className="hand__summary">{handoff.summary}</p>}
        </div>

        {failed && <p className="turn__note" role="alert">تعذّر فتح هذه المحادثة. قد لا تملك صلاحية عرضها.</p>}
        {!failed && messages === null && <p className="turn__note" role="status">جارٍ تحميل الرسائل…</p>}
        {messages !== null && messages.length === 0 && <p className="turn__note">لم تصل ردود بعد.</p>}

        <ol className="hand__list" aria-live="polite">
          {messages?.map((m) => (
            <li key={m.sequence} className={`hand__msg${m.mine ? ' hand__msg--mine' : ''}`}>
              {!m.mine && <span className="hand__who">{departmentName}</span>}
              <p>{m.body}</p>
            </li>
          ))}
        </ol>
        <div ref={end} />
      </div>

      <div className="composer">
        {closed && <p className="composer__blocked">أُغلقت هذه المحادثة.</p>}
        {sendError && <p className="composer__blocked" role="alert">تعذّر إرسال الرسالة. حاول مرة أخرى.</p>}
        <div className="composer__row">
          <textarea
            className="composer__input"
            rows={1}
            value={draft}
            maxLength={8000}
            disabled={closed || !handoff}
            placeholder="اكتب ردّك…"
            aria-label="رسالتك إلى القسم"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }}
          />
          <button type="button" className="composer__send" disabled={closed || !draft.trim() || sending} onClick={() => void send()}>
            إرسال
          </button>
        </div>
      </div>
    </>
  )
}
