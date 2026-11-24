import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import {
  ask, createConversation, getMe, getMyDepartmentName, getUsage, listConversations, listTurns,
  type Answer, type Conversation, type Me, type Usage,
} from '../api/chat'
import { createHandoff } from '../api/handoffs'
import { current, logout } from '../auth/session'
import ThemeToggle from '../components/ThemeToggle'
import Composer from '../chat/Composer'
import { askErrorMessage, fmtNumber } from '../chat/format'
import HandoffThread from '../chat/HandoffThread'
import Inbox from '../chat/Inbox'
import Sidebar from '../chat/Sidebar'
import Thread, { type Turn } from '../chat/Thread'
import './Chat.css'

const uid = () => crypto.randomUUID()
const LOW_CREDIT = 5

export default function Chat() {
  const navigate = useNavigate()
  const signedIn = Boolean(current())

  const [me, setMe] = useState<Me | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [deptName, setDeptName] = useState<string | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [loadingThread, setLoadingThread] = useState(false)
  const [bootError, setBootError] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [handoffId, setHandoffId] = useState<string | null>(null)
  const [inboxTick, setInboxTick] = useState(0)
  const conversationFor = useRef(new Map<string, string>()) // turn key -> conversation id

  // The first department the person holds a role in. Names are only exposed to managers, so others see a generic label.
  const departmentId = useMemo(() => me?.roles.find((r) => r.scope === 'department' && r.department_id)?.department_id ?? null, [me])
  const departmentName = deptName ?? 'قسمك'

  const refreshUsage = useCallback(() => getUsage().then(setUsage).catch(() => undefined), [])

  useEffect(() => {
    if (!signedIn) return
    let alive = true
    Promise.all([getMe(), getUsage().catch(() => null), listConversations(), getMyDepartmentName()])
      .then(([profile, u, list, dept]) => {
        if (!alive) return
        setMe(profile)
        setUsage(u)
        setConversations(list.items)
        const id = profile.roles.find((r) => r.scope === 'department' && r.department_id)?.department_id
        if (dept && dept.id === id) setDeptName(dept.name)
      })
      .catch(() => alive && setBootError(true))
    return () => { alive = false }
  }, [signedIn])

  if (!signedIn) return <Navigate to="/login" replace />

  async function openConversation(id: string) {
    setMenuOpen(false)
    setHandoffId(null)
    setActiveId(id)
    setTurns([])
    setLoadingThread(true)
    try {
      const { items } = await listTurns(id)
      setTurns(
        items.map<Turn>((t) =>
          t.content_status === 'available' && t.response
            ? { key: t.turn_id, question: t.question ?? '', state: 'done', response: t.response }
            : {
                key: t.turn_id,
                question: t.question ?? 'رسالة',
                state: 'unavailable',
                note: t.content_status === 'access_revoked'
                  ? 'لم تعد تملك صلاحية عرض هذه الرسالة.'
                  : 'تعذّر التحقق من هذه الرسالة الآن.',
              },
        ),
      )
    } catch {
      setTurns([])
    } finally {
      setLoadingThread(false)
    }
  }

  function newConversation() {
    setHandoffId(null)
    setActiveId(null)
    setTurns([])
    setMenuOpen(false)
  }

  async function run(turnKey: string, question: string, knownConversation: string | null) {
    if (!departmentId) return
    try {
      let conversationId = knownConversation
      if (!conversationId) {
        const created = await createConversation(departmentId, uid())
        conversationId = created.conversation_id
        conversationFor.current.set(turnKey, conversationId)
        setActiveId(conversationId)
        setConversations((c) => (c.some((x) => x.conversation_id === created.conversation_id) ? c : [...c, created]))
      }
      const response = await ask({ question, departmentId, conversationId, requestKey: turnKey })
      setTurns((t) => t.map((x) => (x.key === turnKey ? { ...x, state: 'done', response, error: undefined } : x)))
      refreshUsage()
    } catch (error) {
      setTurns((t) => t.map((x) => (x.key === turnKey ? { ...x, state: 'error', error: askErrorMessage(error) } : x)))
    }
  }

  function send(question: string) {
    const key = uid() // doubles as the idempotency key, so a retry never charges twice
    setTurns((t) => [...t, { key, question, state: 'pending' }])
    void run(key, question, activeId)
  }

  function retry(key: string) {
    const turn = turns.find((t) => t.key === key)
    if (!turn) return
    setTurns((t) => t.map((x) => (x.key === key ? { ...x, state: 'pending', error: undefined } : x)))
    void run(key, turn.question, conversationFor.current.get(key) ?? activeId)
  }

  async function handOff(answer: Answer, summary: string) {
    if (!departmentId) throw new Error('no department')
    // The stored sequence of this turn is only known to the server, so read it back from the conversation.
    const { items } = await listTurns(answer.conversation_id)
    const turn = items.find((t) => t.turn_id === answer.question_request_id) ?? items[items.length - 1]
    if (!turn) throw new Error('no turn')
    const created = await createHandoff({
      requestKey: `handoff-${answer.question_request_id}`,
      conversationId: answer.conversation_id,
      turnSequence: turn.sequence,
      departmentId,
      summary,
    })
    setInboxTick((n) => n + 1)
    setHandoffId(created.handoff_id)
  }

  const pending = turns.some((t) => t.state === 'pending')
  const daily = usage?.configured ? usage.daily_remaining : null
  const monthly = usage?.configured ? usage.monthly_remaining : null
  const blockedReason = !departmentId && me
    ? 'لا يوجد قسم مرتبط بحسابك. تواصل مع مسؤول النظام.'
    : daily === 0
      ? 'انتهى رصيد أسئلتك لهذا اليوم.'
      : monthly === 0
        ? 'انتهى رصيد أسئلتك لهذا الشهر.'
        : usage?.company_budget_blocked
          ? 'بلغت الشركة حدّ ميزانية الأسئلة لهذا الشهر.'
          : undefined

  if (bootError) {
    return (
      <main className="boot">
        <p role="alert">تعذّر تحميل بياناتك. تحقق من الاتصال ثم أعد تحميل الصفحة.</p>
        <button type="button" className="link-btn" onClick={() => window.location.reload()}>إعادة التحميل</button>
      </main>
    )
  }

  return (
    <div className="chat">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        open={menuOpen}
        onNew={newConversation}
        onSelect={openConversation}
        onClose={() => setMenuOpen(false)}
      />
      {menuOpen && <button type="button" className="scrim" aria-label="إغلاق القائمة" onClick={() => setMenuOpen(false)} />}

      <div className="chat__main">
        <header className="bar">
          <button type="button" className="bar__menu" onClick={() => setMenuOpen(true)} aria-label="فتح قائمة المحادثات">☰</button>
          <span className="dept-chip">{departmentName}</span>
          <span className="bar__sp" />
          <ThemeToggle />
          <Inbox
            refreshKey={inboxTick}
            departmentName={(id) => (id === departmentId ? departmentName : 'القسم')}
            onOpen={(id) => { setMenuOpen(false); setHandoffId(id) }}
          />
          {daily !== null && (
            <span className={`credits${daily <= LOW_CREDIT ? ' credits--low' : ''}`} title={monthly !== null ? `المتبقي هذا الشهر: ${fmtNumber(monthly)}` : undefined}>
              المتبقي اليوم <strong>{fmtNumber(daily)}</strong> {daily === 1 ? 'سؤال' : 'أسئلة'}
            </span>
          )}
          {me?.roles.some((r) => r.name === 'Admin' && r.scope === 'global') && (
            <Link className="bar__admin" to="/admin">لوحة الأدمن</Link>
          )}
          <span className="bar__user">{me?.name}</span>
          <button
            type="button"
            className="bar__out"
            onClick={async () => {
              await logout()
              navigate('/login', { replace: true })
            }}
          >
            خروج
          </button>
        </header>

        {handoffId ? (
          <HandoffThread
            handoffId={handoffId}
            departmentName={departmentName}
            onRead={() => setInboxTick((n) => n + 1)}
            onBack={() => setHandoffId(null)}
          />
        ) : (
          <>
            <Thread turns={turns} loading={loadingThread} departmentName={departmentName} onRetry={retry} onHandoff={handOff} />
            <Composer disabled={pending || !me || !departmentId} blockedReason={blockedReason} onSend={send} />
          </>
        )}
      </div>
    </div>
  )
}
