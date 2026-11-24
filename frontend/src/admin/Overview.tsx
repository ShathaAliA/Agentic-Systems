import { useEffect, useState } from 'react'
import { getCompanyUsage, type CompanyUsage } from '../api/admin'
import { fmtNumber } from '../chat/format'

const usd = new Intl.NumberFormat('ar-EG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const money = (micro: number | null) => (micro === null ? '—' : `${usd.format(micro / 1_000_000)} دولار`)

export default function Overview() {
  const [usage, setUsage] = useState<CompanyUsage | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    getCompanyUsage().then(setUsage).catch(() => setFailed(true))
  }, [])

  if (failed) return <p className="ad-state" role="alert">تعذّر تحميل ملخص الاستخدام.</p>
  if (!usage) return <p className="ad-state" role="status">جارٍ التحميل…</p>
  if (!usage.configured) {
    return (
      <section className="ad-panel">
        <h2 className="ad-h2">ميزانية الأسئلة</h2>
        <p className="ad-state">لم تُضبط سياسة الاستخدام بعد، فلا توجد ميزانية لعرضها.</p>
      </section>
    )
  }

  const budget = usage.budget_micro_usd ?? 0
  const spent = usage.spent_micro_usd ?? 0
  const held = usage.held_micro_usd ?? 0
  const share = budget > 0 ? Math.min(100, Math.round(((spent + held) / budget) * 100)) : 0
  const over = (usage.over_budget_micro_usd ?? 0) > 0
  const unresolved = usage.unresolved_requests ?? 0

  return (
    <section className="ad-panel" aria-labelledby="usage-title">
      <h2 id="usage-title" className="ad-h2">ميزانية الأسئلة{usage.month ? ` · ${usage.month}` : ''}</h2>
      <p className="ad-lead">
        استُهلك <strong>{money(spent + held)}</strong> من <strong>{money(budget)}</strong>
        {budget > 0 && <> ({fmtNumber(share)}٪)</>}
        {!usage.enabled && ' · معالجة الأسئلة متوقفة'}
      </p>
      <progress className={`ad-meter${over ? ' ad-meter--over' : ''}`} max={100} value={share} aria-label="نسبة استهلاك الميزانية" />
      <table className="ad-table ad-table--plain">
        <tbody>
          <tr><th scope="row">المصروف</th><td>{money(usage.spent_micro_usd)}</td></tr>
          <tr><th scope="row">المحجوز لأسئلة قيد المعالجة</th><td>{money(usage.held_micro_usd)}</td></tr>
          <tr><th scope="row">المتبقي</th><td>{money(usage.remaining_micro_usd)}</td></tr>
          {over && <tr className="ad-row--alert"><th scope="row">تجاوز الميزانية</th><td>{money(usage.over_budget_micro_usd)}</td></tr>}
          <tr className={unresolved > 0 ? 'ad-row--alert' : undefined}>
            <th scope="row">طلبات غير محسومة</th>
            <td>{fmtNumber(unresolved)}</td>
          </tr>
        </tbody>
      </table>
    </section>
  )
}
