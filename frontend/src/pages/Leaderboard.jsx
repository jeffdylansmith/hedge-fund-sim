import { useState, useEffect, useCallback } from 'react'

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

function fmt$(n, decimals = 0) {
  return '$' + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: decimals })
}

// ── Positions modal ───────────────────────────────────────────────────────────

function PositionsModal({ traderId, onClose }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const meta = TRADER_META[traderId]

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(`${API}/traders/${traderId}/positions`)
      .then(r => {
        if (!r.ok) throw new Error(`${r.status}`)
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [traderId])

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const pnlColor = v => v >= 0 ? '#68d391' : '#fc8181'

  return (
    // Backdrop
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,0.65)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 24,
      }}
    >
      {/* Panel — stop clicks from closing */}
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#161b27', border: '1px solid #1e2535', borderRadius: 12,
          width: '100%', maxWidth: 780, maxHeight: '80vh',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Modal header */}
        <div style={{
          padding: '18px 24px', borderBottom: '1px solid #1e2535',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 38, height: 38, borderRadius: '50%',
              background: meta.color + '20', border: `2px solid ${meta.color}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 15, fontWeight: 700, color: meta.color, flexShrink: 0,
            }}>{meta.initial}</div>
            <span style={{ fontSize: 16, fontWeight: 700, color: meta.color }}>
              {meta.name}'s Positions
            </span>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: '#4a5568',
              fontSize: 20, cursor: 'pointer', lineHeight: 1, padding: '2px 6px',
            }}
          >✕</button>
        </div>

        {/* Modal body */}
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {loading && (
            <div style={{ padding: 40, textAlign: 'center', color: '#4a5568', fontSize: 13 }}>
              Loading…
            </div>
          )}

          {error && (
            <div style={{ padding: 40, textAlign: 'center', color: '#fc8181', fontSize: 13 }}>
              Failed to load positions: {error}
            </div>
          )}

          {data && data.positions.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', color: '#4a5568', fontSize: 13 }}>
              No open positions.
            </div>
          )}

          {data && data.positions.length > 0 && (
            <>
              {/* Summary bar */}
              <div style={{
                display: 'flex', gap: 0,
                borderBottom: '1px solid #1e2535',
              }}>
                {[
                  { label: 'Total Value',    value: fmt$(data.summary.total_market_value),  color: '#e2e8f0' },
                  { label: 'Total Cost',     value: fmt$(data.summary.total_cost_basis),    color: '#e2e8f0' },
                  { label: 'Unrealized P&L', value: (data.summary.total_unrealized_pnl >= 0 ? '+' : '') + fmt$(data.summary.total_unrealized_pnl), color: pnlColor(data.summary.total_unrealized_pnl) },
                ].map((s, i) => (
                  <div key={i} style={{
                    flex: 1, padding: '14px 20px',
                    borderRight: i < 2 ? '1px solid #1e2535' : 'none',
                  }}>
                    <div style={{ fontSize: 10, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
                      {s.label}
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: s.color }}>
                      {s.value}
                    </div>
                  </div>
                ))}
              </div>

              {/* Positions table */}
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1e2535' }}>
                    {['Ticker', 'Shares', 'Avg Cost', 'Current Price', 'Market Value', 'Unrealized P&L', 'P&L %'].map(h => (
                      <th key={h} style={{
                        padding: '10px 16px', textAlign: h === 'Ticker' ? 'left' : 'right',
                        fontSize: 10, fontWeight: 600, color: '#4a5568',
                        textTransform: 'uppercase', letterSpacing: '0.07em', whiteSpace: 'nowrap',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.positions.map((pos, idx) => (
                    <tr
                      key={pos.ticker}
                      style={{ borderBottom: idx < data.positions.length - 1 ? '1px solid #1a2030' : 'none' }}
                    >
                      <td style={{ padding: '12px 16px', fontWeight: 700, fontSize: 13, color: '#e2e8f0' }}>
                        {pos.ticker}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontSize: 13, color: '#a0aec0' }}>
                        {pos.shares}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontSize: 13, color: '#a0aec0' }}>
                        ${pos.avg_cost.toFixed(2)}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontSize: 13, color: pos.price_is_stale ? '#718096' : '#a0aec0' }}>
                        {pos.price_is_stale ? '~' : ''}{pos.current_price.toFixed(2)}
                        {pos.price_is_stale && (
                          <span style={{ fontSize: 10, color: '#4a5568', marginLeft: 4 }}>est.</span>
                        )}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontSize: 13, color: '#a0aec0' }}>
                        {fmt$(pos.market_value)}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontSize: 13, fontWeight: 600, color: pnlColor(pos.unrealized_pnl) }}>
                        {pos.unrealized_pnl >= 0 ? '+' : ''}{fmt$(pos.unrealized_pnl)}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', fontSize: 13, color: pnlColor(pos.unrealized_pnl_pct) }}>
                        {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Leaderboard page ──────────────────────────────────────────────────────────

export default function Leaderboard() {
  const [traders,        setTraders]        = useState([])
  const [sort,           setSort]           = useState({ key: 'pnl', dir: 'desc' })
  const [modalTraderId,  setModalTraderId]  = useState(null)

  useEffect(() => {
    fetch(`${API}/traders`).then(r => r.json()).then(setTraders)
  }, [])

  function toggleSort(key) {
    if (!key) return
    setSort(s => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })
  }

  const closeModal = useCallback(() => setModalTraderId(null), [])

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
        <p style={{ fontSize: 13, color: '#4a5568' }}>Click a column header to sort · Click a row to see positions</p>
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
                    userSelect: 'none', whiteSpace: 'nowrap',
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
              const wl = t.wins + t.losses > 0 ? `${t.wins}W / ${t.losses}L` : '—'
              return (
                <tr
                  key={t.trader_id}
                  onClick={() => setModalTraderId(t.trader_id)}
                  style={{
                    borderBottom: idx < sorted.length - 1 ? '1px solid #1a2030' : 'none',
                    cursor: 'pointer',
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = '#1a2030'}
                  onMouseLeave={e => e.currentTarget.style.background = ''}
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
                    <span style={{ fontSize: 15, fontWeight: 700, color: t.pnl_pct >= 0 ? '#68d391' : '#fc8181' }}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%
                    </span>
                  </td>
                  {/* P&L */}
                  <td style={{ padding: '18px 20px', textAlign: 'right' }}>
                    <span style={{ fontSize: 14, color: t.pnl >= 0 ? '#68d391' : '#fc8181' }}>
                      {t.pnl >= 0 ? '+' : ''}{fmt$(t.pnl)}
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
                    {fmt$(t.cash)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {modalTraderId && (
        <PositionsModal traderId={modalTraderId} onClose={closeModal} />
      )}
    </div>
  )
}
