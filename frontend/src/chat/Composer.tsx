import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

const MAX = 4000

/** A question typed on the landing page is carried here once, then forgotten. */
function takeDraft(): string {
  try {
    const q = sessionStorage.getItem('ai-platform.draft') ?? ''
    sessionStorage.removeItem('ai-platform.draft')
    return q
  } catch {
    return ''
  }
}

interface Props {
  disabled: boolean
  blockedReason?: string
  onSend: (question: string) => void
}

export default function Composer({ disabled, blockedReason, onSend }: Props) {
  const [text, setText] = useState(takeDraft)
  const field = useRef<HTMLTextAreaElement>(null)
  const blocked = disabled || Boolean(blockedReason)

  function submit(e?: FormEvent) {
    e?.preventDefault()
    const question = text.trim()
    if (!question || blocked) return
    onSend(question)
    setText('')
    if (field.current) field.current.style.height = ''
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  function grow(el: HTMLTextAreaElement) {
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }

  return (
    <form className="composer" onSubmit={submit}>
      {blockedReason && <p className="composer__blocked" role="status">{blockedReason}</p>}
      <div className="composer__row">
        <label className="visually-hidden" htmlFor="question">سؤالك</label>
        <textarea
          id="question"
          ref={field}
          className="composer__input"
          rows={1}
          maxLength={MAX}
          placeholder="اكتب سؤالك عن مستندات قسمك…"
          value={text}
          disabled={Boolean(blockedReason)}
          onChange={(e) => {
            setText(e.target.value)
            grow(e.target)
          }}
          onKeyDown={onKeyDown}
        />
        <button className="composer__send" type="submit" disabled={blocked || !text.trim()}>
          إرسال
        </button>
      </div>
      <p className="composer__hint">Enter للإرسال · Shift + Enter لسطر جديد</p>
    </form>
  )
}
