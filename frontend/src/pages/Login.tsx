import { lazy, Suspense, useId, useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import ThemeToggle from '../components/ThemeToggle'
import { current, login, type LoginFailure } from '../auth/session'
import './Login.css'

const GradientBackground = lazy(() => import('../components/GradientBackground'))

const MESSAGES: Record<LoginFailure, (retryAfter?: number) => string> = {
  invalid: () => 'البريد الإلكتروني أو كلمة المرور غير صحيحة.',
  'rate-limited': (s) =>
    s ? `محاولات كثيرة. أعد المحاولة بعد ${s} ثانية.` : 'محاولات كثيرة. أعد المحاولة بعد قليل.',
  unavailable: () => 'تعذّر الاتصال بالخدمة. تحقق من الاتصال وأعد المحاولة.',
}

export default function Login() {
  const navigate = useNavigate()
  const ids = { email: useId(), password: useId(), emailError: useId(), passwordError: useId() }
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({})
  const [busy, setBusy] = useState(false)

  if (current()) return <Navigate to="/app" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    const next: typeof errors = {}
    if (!email.trim()) next.email = 'أدخل البريد الإلكتروني.'
    if (!password) next.password = 'أدخل كلمة المرور.'
    setErrors(next)
    if (next.email || next.password) return

    setBusy(true)
    const result = await login(email.trim(), password)
    setBusy(false)
    if (result.ok) {
      navigate('/app', { replace: true })
    } else {
      setPassword('')
      setErrors({ form: MESSAGES[result.reason](result.retryAfter) })
    }
  }

  return (
    <div className="auth world-bg">
      <Suspense fallback={null}>
        <GradientBackground />
      </Suspense>
      <header className="auth__bar">
        <Link className="auth__logo" to="/">
          <svg viewBox="0 0 34 34" width="30" height="30" aria-hidden="true">
            <rect x="1" y="1" width="32" height="32" rx="9" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="M9 22V12l8 6 8-6v10" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          منصة الذكاء الاصطناعي
        </Link>
        <ThemeToggle />
      </header>

      <main className="auth__main">
        <form className="card" onSubmit={onSubmit} noValidate aria-labelledby="login-title">
          <h1 id="login-title" className="card__title">تسجيل الدخول</h1>
          <p className="card__lead">ادخل ببريدك في الشركة للوصول إلى مستندات قسمك.</p>

          {errors.form && (
            <p className="alert" role="alert">{errors.form}</p>
          )}

          <div className="field">
            <label htmlFor={ids.email}>البريد الإلكتروني</label>
            <input
              id={ids.email}
              className="input input--ltr"
              type="email"
              dir="ltr"
              inputMode="email"
              autoComplete="username"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={Boolean(errors.email)}
              aria-describedby={errors.email ? ids.emailError : undefined}
              placeholder="name@company.com"
            />
            {errors.email && <span id={ids.emailError} className="field__error">{errors.email}</span>}
          </div>

          <div className="field">
            <label htmlFor={ids.password}>كلمة المرور</label>
            <div className="input-wrap">
              <input
                id={ids.password}
                className="input input--ltr"
                type={showPassword ? 'text' : 'password'}
                dir="ltr"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={Boolean(errors.password)}
                aria-describedby={errors.password ? ids.passwordError : undefined}
              />
              <button
                type="button"
                className="input-wrap__toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-pressed={showPassword}
              >
                {showPassword ? 'إخفاء' : 'إظهار'}
              </button>
            </div>
            {errors.password && <span id={ids.passwordError} className="field__error">{errors.password}</span>}
          </div>

          <button className="submit" type="submit" disabled={busy}>
            {busy ? 'جارٍ التحقق…' : 'تسجيل الدخول'}
          </button>

          <p className="card__note">
            هذا تسجيل دخول تجريبي لبيئة التطوير. الدخول عبر Microsoft Entra قيد الإعداد.
          </p>
        </form>

        <Link className="auth__back" to="/">العودة إلى الصفحة الرئيسية</Link>
      </main>
    </div>
  )
}
