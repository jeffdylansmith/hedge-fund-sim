import { useState, useEffect } from 'react'

const API = import.meta.env.VITE_API_URL

const TRADER_META = {
  alex: {
    name: 'Alex',
    color: '#FF6B6B',
    initial: 'A',
    style: 'Aggressive Momentum',
    personality: 'Alex chases price action and acts decisively on strong signals. High conviction, high velocity — if the tape says go, Alex goes.',
    risk: 'High — accepts larger drawdowns in pursuit of outsized returns.',
  },
  jordan: {
    name: 'Jordan',
    color: '#4299E1',
    initial: 'J',
    style: 'Conservative Macro',
    personality: 'Jordan waits for high-conviction setups and rarely forces trades. Capital preservation is the north star — only deploys when multiple factors align.',
    risk: 'Low — prioritizes capital preservation above all else.',
  },
  casey: {
    name: 'Casey',
    color: '#9F7AEA',
    initial: 'C',
    style: 'Contrarian',
    personality: 'Casey fades consensus moves and looks for overreactions to exploit. Buys fear, sells greed — but sizes carefully because being early feels the same as being wrong.',
    risk: 'Medium — buys fear and sells greed with disciplined position sizing.',
  },
}

function parseQuote(decision) {
  if (!decision) return null
  if (decision.action === 'portfolio_review') {
    try {
      const decisions = JSON.parse(decision.reasoning)
      const nonHold = decisions.filter(d => d.action !== 'HOLD')
      if (nonHold.length > 0) return nonHold[0].reasoning
      return decisions[0]?.reasoning ?? null
    } catch { return null }
  }
  const text = decision.reasoning ?? ''
  return text.split(/\.\s+/)[0].trimEnd().replace(/\.$/, '') + '.'
}

export default function Team() {
  const [traders, setTraders] = useState([])

  function fetchData() {
    fetch(`${API}/traders`).then(r => r.json()).then(setTraders)
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 60_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>The Team</h1>
        <p style={{ fontSize: 13, color: '#4a5568' }}>Three traders. One fund. Very different opinions.</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
        {traders.map(t => {
          const m = TRADER_META[t.trader_id]
          if (!m) return null
          const quote = parseQuote(t.recent_decision)
          const gain = t.pnl >= 0
          return (
            <div key={t.trader_id} style={{
              background: '#161b27',
              border: `1px solid ${m.color}30`,
              borderRadius: 12,
              padding: 28,
              display: 'flex',
              flexDirection: 'column',
              gap: 0,
            }}>
              {/* Avatar + name */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20 }}>
                <div style={{
                  width: 56, height: 56, borderRadius: '50%',
                  background: m.color + '18', border: `3px solid ${m.color}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 22, fontWeight: 700, color: m.color, flexShrink: 0,
                }}>{m.initial}</div>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#e2e8f0', lineHeight: 1.2 }}>{m.name}</div>
                  <div style={{
                    marginTop: 4, display: 'inline-block',
                    fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                    background: m.color + '18', color: m.color, letterSpacing: '0.06em',
                  }}>{m.style.toUpperCase()}</div>
                </div>
              </div>

              {/* P&L */}
              <div style={{
                display: 'flex', gap: 16, marginBottom: 20,
                padding: '14px 16px', background: '#0f1117', borderRadius: 8,
                border: '1px solid #1a2030',
              }}>
                <div>
                  <div style={{ fontSize: 10, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>P&L</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: gain ? '#68d391' : '#fc8181' }}>
                    {gain ? '+' : ''}${Math.abs(t.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </div>
                </div>
                <div style={{ borderLeft: '1px solid #1e2535', paddingLeft: 16 }}>
                  <div style={{ fontSize: 10, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>Return</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: gain ? '#68d391' : '#fc8181' }}>
                    {gain ? '+' : ''}{t.pnl_pct}%
                  </div>
                </div>
                <div style={{ borderLeft: '1px solid #1e2535', paddingLeft: 16 }}>
                  <div style={{ fontSize: 10, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>Trades</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#a0aec0' }}>{t.trade_count}</div>
                </div>
              </div>

              {/* Personality */}
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 11, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>Profile</div>
                <p style={{ fontSize: 13, color: '#a0aec0', lineHeight: 1.6, margin: 0 }}>{m.personality}</p>
              </div>

              {/* Risk */}
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 11, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Risk Tolerance</div>
                <p style={{ fontSize: 13, color: '#718096', lineHeight: 1.5, margin: 0 }}>{m.risk}</p>
              </div>

              {/* Latest quote */}
              {quote && (
                <div style={{
                  marginTop: 'auto',
                  padding: '14px 16px',
                  background: m.color + '0a',
                  borderLeft: `3px solid ${m.color}`,
                  borderRadius: '0 6px 6px 0',
                }}>
                  <div style={{ fontSize: 11, color: '#4a5568', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Latest take</div>
                  <p style={{ fontSize: 12, color: '#a0aec0', lineHeight: 1.55, margin: 0, fontStyle: 'italic' }}>
                    "{quote}"
                  </p>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
