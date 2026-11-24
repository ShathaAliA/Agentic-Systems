import { useEffect, useRef } from 'react'
import type { Answer as AnswerData } from '../api/chat'
import Answer from './Answer'

export interface Turn {
  key: string
  question: string
  state: 'pending' | 'done' | 'error' | 'unavailable'
  response?: AnswerData
  error?: string
  note?: string
}

interface Props {
  turns: Turn[]
  loading: boolean
  departmentName: string
  onRetry: (key: string) => void
  onHandoff: (data: AnswerData, summary: string) => Promise<void>
}

export default function Thread({ turns, loading, departmentName, onRetry, onHandoff }: Props) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [turns])

  if (loading) return <div className="thread thread--empty" role="status">جارٍ تحميل المحادثة…</div>

  if (turns.length === 0) {
    return (
      <div className="thread thread--empty">
        <h2 className="empty__title">اسأل عن مستندات {departmentName}</h2>
        <p className="empty__text">تُجاب أسئلتك من مستندات قسمك المعتمدة فقط، وتُعرض معها مصادرها.</p>
      </div>
    )
  }

  return (
    <div className="thread" aria-live="polite">
      {turns.map((t) => (
        <section key={t.key} className="turn">
          <p className="bubble">{t.question}</p>
          {t.state === 'pending' && <p className="pending" role="status">جارٍ البحث في مستندات القسم…</p>}
          {t.state === 'done' && t.response && <Answer data={t.response} question={t.question} departmentName={departmentName} onHandoff={onHandoff} />}
          {t.state === 'unavailable' && <p className="turn__note">{t.note}</p>}
          {t.state === 'error' && (
            <div className="turn__error" role="alert">
              <span>{t.error}</span>
              <button type="button" className="link-btn" onClick={() => onRetry(t.key)}>إعادة المحاولة</button>
            </div>
          )}
        </section>
      ))}
      <div ref={end} />
    </div>
  )
}
