import { useEffect, useState } from 'react'
import { api } from '../api'
import { useNav } from '../nav'
import { useAuth } from '../auth'
import { TopBar } from '../ui'
import { MODULES } from '../modules'
import type { DashboardData, ModuleKey } from '../types'

export default function Modules() {
  const { go } = useNav()
  const { can, user } = useAuth()
  const [totals, setTotals] = useState<Record<string, number>>({})
  const [attn, setAttn] = useState<Record<string, number>>({})

  useEffect(() => {
    api<DashboardData>('/api/dashboard').then((d) => { setTotals(d.moduleTotals); setAttn(d.moduleAttention || {}) }).catch(() => {})
  }, [])

  const keys = (Object.keys(MODULES) as ModuleKey[]).filter((k) => can(k))

  return (
    <div className="screen">
      <TopBar title="All modules" sub={`${keys.length} tools · everything in one place`} />
      <div className="mod-grid">
        {keys.map((k) => {
          const m = MODULES[k]
          const Icon = m.Icon
          const total = totals[k]
          return (
            <button key={k} className="mod-tile" onClick={() => go(k)}>
              <span className="mod-glow" style={{ background: m.color }} />
              <div className="mod-ic" style={{ background: m.color }}>
                <Icon />
                {attn[k] > 0 && <span className={`attn-badge${attn[k] >= 3 ? ' pulse' : ''}`}>{attn[k] > 9 ? '9+' : attn[k]}</span>}
              </div>
              <div className="mod-txt">
                <div className="mod-name">{m.label}</div>
                <div className="mod-metric">{m.metric ? m.metric(total ?? 0) : `${total ?? 0} items`}</div>
              </div>
            </button>
          )
        })}
        {user?.can_admin && (
          <button className="mod-tile" onClick={() => go('admin')}>
            <span className="mod-glow" style={{ background: '#334155' }} />
            <div className="mod-ic" style={{ background: 'linear-gradient(135deg,#334155,#0f172a)', fontSize: 22 }}>⚙️</div>
            <div className="mod-txt">
              <div className="mod-name">Admin</div>
              <div className="mod-metric">Users &amp; access</div>
            </div>
          </button>
        )}
      </div>
    </div>
  )
}
