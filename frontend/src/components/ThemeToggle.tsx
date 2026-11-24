import { setTheme, useTheme } from '../theme'
import './ThemeToggle.css'

export default function ThemeToggle({ className = '' }: { className?: string }) {
  const theme = useTheme()
  const toLight = theme === 'dark'
  return (
    <button
      type="button"
      className={`theme-toggle ${className}`}
      aria-label={toLight ? 'التبديل إلى الوضع الفاتح' : 'التبديل إلى الوضع الداكن'}
      title={toLight ? 'الوضع الفاتح' : 'الوضع الداكن'}
      onClick={() => setTheme(toLight ? 'light' : 'dark')}
    >
      {toLight ? (
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M19.5 14.6A8 8 0 0 1 9.4 4.5a8 8 0 1 0 10.1 10.1z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      )}
    </button>
  )
}
