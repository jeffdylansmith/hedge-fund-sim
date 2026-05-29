import { useState, useEffect } from 'react'

const API = import.meta.env.VITE_API_URL

const TRADER_META = {
  alex:   { name: 'Alex',   style: 'Aggressive Momentum', color: '#FF6B6B', initial: 'A' },
  jordan: { name: 'Jordan', style: 'Conservative Macro',  color: '#4299E1', initial: 'J' },
  casey:  { name: 'Casey',  style: 'Contrarian',          color: '#9F7AEA', initial: 'C' },
}

const COLS = [
  { key: 'name',       label: 'Trader',        sortKey: null },
  { key: 'pnl_pct',   label: 'Total Return',   sortKey: 'pnl_pct' },
  { key: 'pnl',       label: 'P&L',            sortKey: 'pnl' },
  { key: 'wl',        label: 'Win / Loss',      sortKey: 'wins' },
  { key: 'trades',    label: 'Trades',          sortKey: 'trade_count' },
  { key: 'cash',      label: 'Cash Remaining',  sortKey: 'cash' },
]

export default function Leaderboard() {
  const [traders, setTraders] = useState([])
  const [sort, setSort] = useState({ key: 'pnl', dir: 'desc' })

  useEffect(() => {
    fetch(`${API}/traders`).then(r => r.json()).then(setTraders)
  }, [])

  function toggleSort(key) {
    if (!key) return
    setSort(s => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })
  }

  const sorted = [...traders].sort((a, b) => {
    const v = sort.dir === 'desc' ? b[sort.key] - a[sort.key] : a[sort.key] - b[sort.key]
    return v
  })

  const arrow = (key) => {
    if (sort.key !== key) return <span style={{ color: '#2d3748' }}> ↕</span>
    return <span style={{ color: '#718096' }}>{sort.dir === 'desc' ? ' ↓' : ' ↑'}</span>
  }

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Leaderboard</h1>
        <p style={{ fontSize: 13, color: '#4a5568' }}>Click a column header to sort</p>
      </div>

      <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e2535' }}>
              {COLS.map(c => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.sortKey)}
                  style={{
                    padding: '14px 20px',
                    textAlign: c.key === 'name' ? 'left' : 'right',
                    fontSize: 11, fontWeight: 600, color: '#4a5568',
                    textTransform: 'uppercase', letterSpacing: '0.07em',
                    cursor: c.sortKey ? 'pointer' : 'default',
                    userSelect: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {c.label}{c.sortKey && arrow(c.sortKey)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((t, idx) => {
              const m = TRADER_META[t.trader_id]
              if (!m) return null
              const wl = t.wins + t.losses > 0
                ? `${t.wins}W / ${t.losses}L`
                : '—'
              return (
                <tr
                  key={t.trader_id}
                  style={{ borderBottom: idx < sorted.length - 1 ? '1px solid #1a2030' : 'none' }}
                >
                  {/* Trader */}
                  <td style={{ padding: '18px 20px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <div style={{
                        width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
                        background: m.color + '20', border: `2px solid ${m.color}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 13, fontWeight: 700, color: m.color,
                      }}>{m.initial}</div>
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 14 }}>{m.name}</div>
                        <div style={{ fontSize: 11, color: '#4a5568', marginTop: 2 }}>{m.style}</div>
                      </div>
                    </div>
                  </td>
                  {/* Return */}
                  <td style={{ padding: '18px 20px', textAlign: 'right' }}>
                    <span style={{
                      fontSize: 15, fontWeight: 700,
                      color: t.pnl_pct >= 0 ? '#68d391' : '#fc8181',
                    }}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%
                    </span>
                  </td>
                  {/* P&L */}
                  <td style={{ padding: '18px 20px', textAlign: 'right' }}>
                    <span style={{ fontSize: 14, color: t.pnl >= 0 ? '#68d391' : '#fc8181' }}>
                      {t.pnl >= 0 ? '+' : ''}${Math.abs(t.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                    </span>
                  </td>
                  {/* Win/Loss */}
                  <td style={{ padding: '18px 20px', textAlign: 'right', fontSize: 13, color: '#a0aec0' }}>
                    {wl}
                  </td>
                  {/* Trades */}
                  <td style={{ padding: '18px 20px', textAlign: 'right', fontSize: 13, color: '#a0aec0' }}>
                    {t.trade_count}
                  </td>
                  {/* Cash */}
                  <td style={{ padding: '18px 20px', textAlign: 'right', fontSize: 13, color: '#a0aec0' }}>
                    ${t.cash.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
