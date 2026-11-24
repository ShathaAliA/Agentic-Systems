import { useState } from 'react'
import type { Answer as AnswerData } from '../api/chat'
import { handoffErrorMessage, locationLabel } from './format'

const NOTES: Partial<Record<AnswerData['status'], string>> = {
  partial: 'إجابة جزئية: لم تتوفر معلومات من بعض الأقسام.',
  conflict: 'تعارض بين مصدرين. راجعهما قبل الاعتماد على الإجابة.',
}

interface Props {
  data: AnswerData
  question: string
  departmentName: string
  onHandoff: (data: AnswerData, summary: string) => Promise<void>
}

function HandoffBox({ data, question, departmentName, onHandoff }: Props) {
  const [open, setOpen] = useState(false)
  const [summary, setSummary] = useState(question)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    if (!summary.trim() || busy) return
    setBusy(true); setError('')
    try {
      await onHandoff(data, summary.trim())
    } catch (e) {
      setError(handoffErrorMessage(e))
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <div className="handoff">
        <p className="handoff__text">لم نجد إجابة في المستندات. يمكنك إرسال سؤالك إلى موظف في {departmentName}.</p>
        <button type="button" className="handoff__btn" onClick={() => setOpen(true)}>تحويل إلى موظف</button>
      </div>
    )
  }
  return (
    <div className="handoff">
      <label className="handoff__label" htmlFor={`handoff-${data.question_request_id}`}>ما الذي تريد أن يعرفه الموظف؟</label>
      <textarea
        id={`handoff-${data.question_request_id}`}
        className="composer__input"
        rows={3}
        maxLength={4000}
        value={summary}
        disabled={busy}
        onChange={(e) => setSummary(e.target.value)}
      />
      {error && <p className="handoff__error" role="alert">{error}</p>}
      <div className="handoff__row">
        <button type="button" className="handoff__btn" disabled={busy || !summary.trim()} onClick={() => void submit()}>
          {busy ? 'جارٍ الإرسال…' : 'إرسال إلى القسم'}
        </button>
        <button type="button" className="link-btn" disabled={busy} onClick={() => setOpen(false)}>إلغاء</button>
      </div>
    </div>
  )
}

export default function Answer({ data, question, departmentName, onHandoff }: Props) {
  const notice = data.status === 'no_answer'
  return (
    <article className={`answer${notice ? ' answer--notice' : ''}`}>
      {NOTES[data.status] && <p className="answer__flag">{NOTES[data.status]}</p>}
      <p className="answer__text">{data.answer}</p>
      {data.answer_kind === 'verbatim_excerpt' && (
        <p className="answer__meta">اقتباس مباشر من المستند، دون توليد.</p>
      )}
      {data.sources.length > 0 && (
        <div className="sources">
          <h3 className="sources__title">المصادر</h3>
          <ul className="sources__list">
            {data.sources.map((s, i) => (
              <li key={`${s.document_id}-${s.version_number}-${i}`}>
                <details className="source">
                  <summary>
                    <span className="source__name">{s.name}</span>
                    <span className="source__loc">{locationLabel(s.location)}</span>
                  </summary>
                  <blockquote className="source__quote">{s.quote}</blockquote>
                </details>
              </li>
            ))}
          </ul>
        </div>
      )}
      {notice && data.handoff_available && (
        <HandoffBox data={data} question={question} departmentName={departmentName} onHandoff={onHandoff} />
      )}
    </article>
  )
}
