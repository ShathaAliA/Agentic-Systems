import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { getMe, type Me } from '../api/chat'
import { current, logout } from '../auth/session'
import ThemeToggle from '../components/ThemeToggle'
import Departments from '../admin/Departments'
import Overview from '../admin/Overview'
import Users from '../admin/Users'
import './Admin.css'

export const isAdmin = (me: Me) => me.roles.some((r) => r.name === 'Admin' && r.scope === 'global')

export default function Admin() {
  const navigate = useNavigate()
  const signedIn = Boolean(current())
  const [me, setMe] = useState<Me | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!signedIn) return
    getMe().then(setMe).catch(() => setFailed(true))
  }, [signedIn])

  if (!signedIn) return <Navigate to="/login" replace />
  if (failed) return <main className="ad-boot"><p role="alert">تعذّر تحميل بياناتك. أعد تحميل الصفحة.</p></main>
  if (!me) return <main className="ad-boot"><p role="status">جارٍ التحميل…</p></main>
  if (!isAdmin(me)) {
    return (
      <main className="ad-boot">
        <h1 className="ad-h2">لا تملك صلاحية الوصول إلى لوحة الأدمن</h1>
        <Link className="ad-btn ad-btn--ghost" to="/app">العودة إلى المحادثة</Link>
      </main>
    )
  }

  return (
    <div className="ad">
      <header className="ad-bar">
        <span className="ad-logo">لوحة الأدمن</span>
        <nav className="ad-nav" aria-label="أقسام اللوحة">
          <NavLink to="/admin" end>نظرة عامة</NavLink>
          <NavLink to="/admin/users">المستخدمون</NavLink>
          <NavLink to="/admin/departments">الأقسام</NavLink>
        </nav>
        <span className="ad-sp" />
        <Link className="ad-link" to="/app">المحادثة</Link>
        <ThemeToggle />
        <span className="ad-user">{me.name}</span>
        <button
          type="button"
          className="ad-btn ad-btn--ghost"
          onClick={async () => {
            await logout()
            navigate('/login', { replace: true })
          }}
        >
          خروج
        </button>
      </header>
      <main className="ad-main">
        <Routes>
          <Route index element={<Overview />} />
          <Route path="users" element={<Users />} />
          <Route path="departments" element={<Departments />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </main>
    </div>
  )
}
