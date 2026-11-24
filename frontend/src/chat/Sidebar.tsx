import type { Conversation } from '../api/chat'
import { fmtWhen } from './format'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  open: boolean
  onNew: () => void
  onSelect: (id: string) => void
  onClose: () => void
}

export default function Sidebar({ conversations, activeId, open, onNew, onSelect, onClose }: Props) {
  // The API lists oldest first; people expect the newest on top.
  const items = [...conversations].reverse()
  return (
    <aside className="side" data-open={open} aria-label="المحادثات">
      <div className="side__top">
        <button type="button" className="side__new" onClick={onNew}>محادثة جديدة</button>
        <button type="button" className="side__close" onClick={onClose} aria-label="إغلاق القائمة">✕</button>
      </div>
      <h2 className="side__title">محادثاتك</h2>
      {items.length === 0 ? (
        <p className="side__empty">لا توجد محادثات بعد.</p>
      ) : (
        <ul className="side__list">
          {items.map((c) => (
            <li key={c.conversation_id}>
              <button
                type="button"
                className="side__item"
                aria-current={c.conversation_id === activeId ? 'true' : undefined}
                onClick={() => onSelect(c.conversation_id)}
              >
                {fmtWhen(c.created_at)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
