import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { PAGE, archiveDepartment, createDepartment, listDepartments, type AdminDepartment } from '../api/admin'
import Modal from './Modal'
import { DEPT_STATUS, adminError } from './messages'

export default function Departments() {
  const [filter, setFilter] = useState('')
  const [items, setItems] = useState<AdminDepartment[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [creating, setCreating] = useState(false)
  const [archiving, setArchiving] = useState<AdminDepartment | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const ids = { name: useId(), filter: useId() }

  const load = useCallback(async (state: string, offset: number) => {
    setLoading(true)
    setFailed(false)
    try {
      const rows = await listDepartments(state, offset)
      setItems((d) => (offset === 0 ? rows : [...d, ...rows]))
      setMore(rows.length === PAGE)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(filter, 0) }, [filter, load])

  const close = () => { setCreating(false); setArchiving(null); setError(''); setName('') }

  async function submitCreate(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return setError('أدخل اسم القسم.')
    setBusy(true)
    setError('')
    try {
      const created = await createDepartment(name.trim())
      setItems((d) => [...d, created])
      close()
    } catch (err) {
      setError(adminError(err))
    } finally {
      setBusy(false)
    }
  }

  async function confirmArchive() {
    if (!archiving) return
    setBusy(true)
    setError('')
    try {
      const updated = await archiveDepartment(archiving.id)
      setItems((d) => d.map((x) => (x.id === updated.id ? updated : x)))
      close()
    } catch (err) {
      setError(adminError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="ad-panel" aria-labelledby="dept-title">
      <div className="ad-head">
        <h2 id="dept-title" className="ad-h2">الأقسام</h2>
        <label className="visually-hidden" htmlFor={ids.filter}>تصفية بالحالة</label>
        <select id={ids.filter} className="ad-select" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">كل الحالات</option>
          {Object.entries(DEPT_STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <button type="button" className="ad-btn ad-btn--primary" onClick={() => setCreating(true)}>قسم جديد</button>
      </div>

      {failed && <p className="ad-state" role="alert">تعذّر تحميل الأقسام.</p>}
      {!failed && !loading && items.length === 0 && <p className="ad-state">لا توجد أقسام بهذه الحالة.</p>}

      {items.length > 0 && (
        <div className="ad-scroll">
          <table className="ad-table">
            <thead>
              <tr><th scope="col">القسم</th><th scope="col">الحالة</th><th scope="col"><span className="visually-hidden">إجراء</span></th></tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td><span className={`ad-badge ad-badge--${d.status}`}>{DEPT_STATUS[d.status] ?? d.status}</span></td>
                  <td>
                    {d.status !== 'archived' && (
                      <button type="button" className="ad-btn ad-btn--ghost" onClick={() => setArchiving(d)}>أرشفة</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {loading && <p className="ad-state" role="status">جارٍ التحميل…</p>}
      {more && !loading && (
        <button type="button" className="ad-btn ad-btn--ghost" onClick={() => void load(filter, items.length)}>تحميل المزيد</button>
      )}

      <Modal open={creating} title="قسم جديد" onClose={close}>
        <form onSubmit={submitCreate} noValidate className="ad-form">
          <p className="ad-note">يبدأ القسم بحالة «قيد التجهيز» إلى أن تكتمل قاعدة بياناته المعزولة.</p>
          <label htmlFor={ids.name}>اسم القسم</label>
          <input id={ids.name} className="ad-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={150} autoFocus />
          {error && <p className="ad-error" role="alert">{error}</p>}
          <div className="ad-actions">
            <button type="button" className="ad-btn ad-btn--ghost" onClick={close}>إلغاء</button>
            <button type="submit" className="ad-btn ad-btn--primary" disabled={busy}>{busy ? 'جارٍ الإنشاء…' : 'إنشاء'}</button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(archiving)} title="أرشفة القسم" onClose={close}>
        <p className="ad-note">سيُؤرشف قسم «{archiving?.name}». الأرشفة لا تحذف أي بيانات.</p>
        {error && <p className="ad-error" role="alert">{error}</p>}
        <div className="ad-actions">
          <button type="button" className="ad-btn ad-btn--ghost" onClick={close}>إلغاء</button>
          <button type="button" className="ad-btn ad-btn--danger" onClick={confirmArchive} disabled={busy}>{busy ? 'جارٍ التنفيذ…' : 'أرشفة'}</button>
        </div>
      </Modal>
    </section>
  )
}
