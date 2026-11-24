import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { PAGE, createUser, listUsers, setUserStatus, type AdminUser } from '../api/admin'
import { fmtWhen } from '../chat/format'
import Modal from './Modal'
import { USER_STATUS, adminError } from './messages'

export default function Users() {
  const [filter, setFilter] = useState('')
  const [users, setUsers] = useState<AdminUser[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [creating, setCreating] = useState(false)
  const [toggle, setToggle] = useState<AdminUser | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const ids = { name: useId(), email: useId(), filter: useId() }

  const load = useCallback(async (status: string, offset: number) => {
    setLoading(true)
    setFailed(false)
    try {
      const rows = await listUsers(status, offset)
      setUsers((u) => (offset === 0 ? rows : [...u, ...rows]))
      setMore(rows.length === PAGE)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(filter, 0) }, [filter, load])

  const close = () => { setCreating(false); setToggle(null); setError(''); setName(''); setEmail('') }

  async function submitCreate(e: FormEvent) {
    e.preventDefault()
    if (!name.trim() || !email.trim()) return setError('أدخل الاسم والبريد الإلكتروني.')
    setBusy(true)
    setError('')
    try {
      const created = await createUser(name.trim(), email.trim())
      setUsers((u) => [...u, created])
      close()
    } catch (err) {
      setError(adminError(err))
    } finally {
      setBusy(false)
    }
  }

  async function confirmToggle() {
    if (!toggle) return
    setBusy(true)
    setError('')
    try {
      const next = toggle.status === 'active' ? 'inactive' : 'active'
      const updated = await setUserStatus(toggle.id, next)
      setUsers((u) => u.map((x) => (x.id === updated.id ? updated : x)))
      close()
    } catch (err) {
      setError(adminError(err))
    } finally {
      setBusy(false)
    }
  }

  const disabling = toggle?.status === 'active'

  return (
    <section className="ad-panel" aria-labelledby="users-title">
      <div className="ad-head">
        <h2 id="users-title" className="ad-h2">المستخدمون</h2>
        <label className="visually-hidden" htmlFor={ids.filter}>تصفية بالحالة</label>
        <select id={ids.filter} className="ad-select" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">كل الحالات</option>
          <option value="active">نشط</option>
          <option value="inactive">معطّل</option>
        </select>
        <button type="button" className="ad-btn ad-btn--primary" onClick={() => setCreating(true)}>مستخدم جديد</button>
      </div>

      {failed && <p className="ad-state" role="alert">تعذّر تحميل المستخدمين.</p>}
      {!failed && !loading && users.length === 0 && <p className="ad-state">لا يوجد مستخدمون بهذه الحالة.</p>}

      {users.length > 0 && (
        <div className="ad-scroll">
          <table className="ad-table">
            <thead>
              <tr><th scope="col">الاسم</th><th scope="col">البريد الإلكتروني</th><th scope="col">الحالة</th><th scope="col">أُنشئ في</th><th scope="col"><span className="visually-hidden">إجراء</span></th></tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.name}</td>
                  <td dir="ltr" className="ad-ltr">{u.email}</td>
                  <td><span className={`ad-badge ad-badge--${u.status}`}>{USER_STATUS[u.status] ?? u.status}</span></td>
                  <td>{fmtWhen(u.created_at)}</td>
                  <td>
                    <button type="button" className="ad-btn ad-btn--ghost" onClick={() => setToggle(u)}>
                      {u.status === 'active' ? 'تعطيل' : 'تفعيل'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {loading && <p className="ad-state" role="status">جارٍ التحميل…</p>}
      {more && !loading && (
        <button type="button" className="ad-btn ad-btn--ghost" onClick={() => void load(filter, users.length)}>تحميل المزيد</button>
      )}

      <Modal open={creating} title="مستخدم جديد" onClose={close}>
        <form onSubmit={submitCreate} noValidate className="ad-form">
          <p className="ad-note">يُنشأ الحساب بلا كلمة مرور. تسجيل بيانات الدخول التجريبية يتم خارج المنصة.</p>
          <label htmlFor={ids.name}>الاسم</label>
          <input id={ids.name} className="ad-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={150} autoFocus />
          <label htmlFor={ids.email}>البريد الإلكتروني</label>
          <input id={ids.email} className="ad-input ad-ltr" type="email" dir="ltr" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={255} />
          {error && <p className="ad-error" role="alert">{error}</p>}
          <div className="ad-actions">
            <button type="button" className="ad-btn ad-btn--ghost" onClick={close}>إلغاء</button>
            <button type="submit" className="ad-btn ad-btn--primary" disabled={busy}>{busy ? 'جارٍ الإنشاء…' : 'إنشاء'}</button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(toggle)} title={disabling ? 'تعطيل المستخدم' : 'تفعيل المستخدم'} onClose={close}>
        <p className="ad-note">
          {disabling
            ? `سيُمنع ${toggle?.name} من الدخول إلى المنصة حتى يُعاد تفعيله.`
            : `سيتمكّن ${toggle?.name} من الدخول إلى المنصة مجددًا.`}
        </p>
        {error && <p className="ad-error" role="alert">{error}</p>}
        <div className="ad-actions">
          <button type="button" className="ad-btn ad-btn--ghost" onClick={close}>إلغاء</button>
          <button type="button" className={`ad-btn ${disabling ? 'ad-btn--danger' : 'ad-btn--primary'}`} onClick={confirmToggle} disabled={busy}>
            {busy ? 'جارٍ التنفيذ…' : disabling ? 'تعطيل' : 'تفعيل'}
          </button>
        </div>
      </Modal>
    </section>
  )
}
