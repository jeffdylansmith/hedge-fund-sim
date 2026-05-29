import { useState, useEffect } from 'react'
import { LineChart, Line, ResponsiveContainer, Tooltip, XAxis } from 'recharts'

const API = import.meta.env.VITE_API_URL

const TRADER_META = {
  alex:   { name: 'Alex',   style: 'Aggressive Momentum', color: '#FF6B6B', initial: 'A' },
  jordan: { name: 'Jordan', style: 'Conservative Macro',  color: '#4299E1', initial: 'J' },
  casey:  { name: 'Casey',  style: 'Contrarian',          color: '#9F7AEA', initial: 'C' },
}

const ACTION_STYLE = {
  BUY:      { bg: '#0d2318', text: '#68d391', border: '#276749' },
  SELL:     { bg: '#2a0f0f', text: '#fc8181', border: '#742a2a' },
  HOLD:     { bg: '#0f1a2e', text: '#90cdf4', border: '#2a4365' },
  ANALYSIS: { bg: '#1a1030', text: '#b794f4', border: '#44337a' },
  REVIEW:   { bg: '#0f1a2e', text: '#90cdf4', border: '#2a4365' },
}

function timeAgo(ts) {
  const s = (Date.now() - new Date(ts)) / 1000
  if (s < 60)   return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function buildSparkline(currentValue) {
  const START = 99_999
  const now = new Date()
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(now)
    d.setDate(d.getDate() - (6 - i))
    const t = i / 6
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
    return {
      date: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      value: Math.round(START + eased * (currentValue - START)),
    }
  })
}

function parseFeedItem(item) {
  const meta = TRADER_META[item.trader_id] ?? { name: item.trader_id, color: '#718096', initial: '?' }
  let action = item.action
  let ticker = item.ticker
  let reasoning = item.reasoning ?? ''
  let confidence = item.confidence

  if (item.action === 'portfolio_review') {
    try {
      const decisions = JSON.parse(item.reasoning)
      const nonHold = decisions.filter(d => d.action !== 'HOLD')
      if (nonHold.length > 0) {
        const d = nonHold[0]
        action = d.action
        ticker = d.ticker
        reasoning = d.reasoning
        confidence = d.confidence
      } else {
        action = 'HOLD'
        ticker = null
        reasoning = 'All positions held — waiting for cleaner signals.'
        confidence = decisions[0]?.confidence ?? null
      }
    } catch {
      action = 'REVIEW'
    }
  } else if (item.action === 'analyze') {
    action = 'ANALYSIS'
    reasoning = reasoning.split(/\.\s+/)[0].trimEnd().replace(/\.$/, '') + '.'
  }

  return { ...item, parsedAction: action, ticker, reasoning, confidence, meta }
}

function StatCard({ label, value, accent }) {
  return (
    <div style={{
      background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: '20px 24px',
    }}>
      <div style={{ fontSize: 11, color: '#4a5568', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700, color: accent || '#e2e8f0', lineHeight: 1.2 }}>
        {value}
      </div>
    </div>
  )
}

export default function Home() {
  const [summary, setSummary] = useState(null)
  const [traders, setTraders] = useState([])
  const [feed, setFeed] = useState([])
  const [sparkData, setSparkData] = useState([])

  async function fetchFeed() {
    const f = await fetch(`${API}/feed?limit=10`).then(r => r.json())
    setFeed(f)
  }

  async function fetchAll() {
    const [s, t, f] = await Promise.all([
      fetch(`${API}/fund/summary`).then(r => r.json()),
      fetch(`${API}/traders`).then(r => r.json()),
      fetch(`${API}/feed?limit=10`).then(r => r.json()),
    ])
    setSummary(s)
    setTraders(t)
    setFeed(f)
    setSparkData(buildSparkline(s.total_fund_value))
  }

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchFeed, 30_000)
    return () => clearInterval(id)
  }, [])

  const sorted = [...traders].sort((a, b) => b.pnl - a.pnl)
  const maxPnl = sorted.length ? Math.max(...sorted.map(t => Math.abs(t.pnl)), 1) : 1
  const gain = summary ? summary.total_fund_value - 99_999 : 0

  return (
    <div>
      {/* ── Stat cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 24 }}>
        <StatCard
          label="Fund Value"
          value={summary ? `$${summary.total_fund_value.toLocaleString()}` : '—'}
        />
        <StatCard
          label="Trades Executed"
          value={summary?.total_trades ?? '—'}
        />
        <StatCard
          label="Decisions Today"
          value={summary?.decisions_today ?? '—'}
        />
        <StatCard
          label="Best Performer"
          value={
            summary?.best_performer
              ? `${TRADER_META[summary.best_performer]?.name}  +${summary.best_performer_pnl?.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
              : '—'
          }
          accent={summary?.best_performer ? TRADER_META[summary.best_performer]?.color : undefined}
        />
      </div>

      {/* ── Standings + Sparkline ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>

        {/* Standings */}
        <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 24 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#4a5568', marginBottom: 20, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
            Trader Standings
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
            {sorted.map(t => {
              const m = TRADER_META[t.trader_id]
              if (!m) return null
              const bar = Math.abs(t.pnl) / maxPnl * 100
              return (
                <div key={t.trader_id}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                    <div style={{
                      width: 34, height: 34, borderRadius: '50%', flexShrink: 0,
                      background: m.color + '20', border: `2px solid ${m.color}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 13, fontWeight: 700, color: m.color,
                    }}>{m.initial}</div>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                        <span style={{ fontWeight: 600, fontSize: 14 }}>{m.name}</span>
                        <span style={{ fontSize: 13, fontWeight: 700, color: t.pnl >= 0 ? '#68d391' : '#fc8181' }}>
                          {t.pnl >= 0 ? '+' : ''}{t.pnl_pct}%
                        </span>
                      </div>
                      <div style={{ fontSize: 11, color: '#4a5568', marginTop: 2 }}>{m.style}</div>
                    </div>
                  </div>
                  <div style={{ height: 4, background: '#1e2535', borderRadius: 2 }}>
                    <div style={{
                      height: '100%', borderRadius: 2, background: m.color,
                      width: `${bar}%`, transition: 'width 0.6s ease',
                    }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Sparkline */}
        <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 24 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#4a5568', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
            Fund Value — 7 Day
          </div>
          {summary && (
            <div style={{ marginBottom: 20 }}>
              <span style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0' }}>
                ${summary.total_fund_value.toLocaleString()}
              </span>
              <span style={{ fontSize: 13, color: gain >= 0 ? '#68d391' : '#fc8181', marginLeft: 10 }}>
                {gain >= 0 ? '+' : ''}${gain.toLocaleString('en-US', { maximumFractionDigits: 0 })}
              </span>
            </div>
          )}
          <ResponsiveContainer width="100%" height={130}>
            <LineChart data={sparkData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: '#4a5568' }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                contentStyle={{ background: '#1e2535', border: 'none', borderRadius: 6, fontSize: 12, color: '#e2e8f0' }}
                formatter={v => [`$${v.toLocaleString()}`, 'Fund Value']}
                labelStyle={{ color: '#718096' }}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke="#68d391"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: '#68d391', strokeWidth: 0 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ── Drama feed ── */}
      <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 24 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: '#4a5568', marginBottom: 20, textTransform: 'uppercase', letterSpacing: '0.07em', display: 'flex', alignItems: 'center', gap: 12 }}>
          Live Feed
          <span style={{ fontWeight: 400, color: '#2d3748' }}>· refreshes every 30s</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {feed.map((item, idx) => {
            const p = parseFeedItem(item)
            const as = ACTION_STYLE[p.parsedAction] ?? ACTION_STYLE.REVIEW
            return (
              <div key={item.id} style={{
                display: 'flex', gap: 14, padding: '14px 0',
                borderBottom: idx < feed.length - 1 ? '1px solid #1a2030' : 'none',
              }}>
                {/* Avatar */}
                <div style={{
                  width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
                  background: p.meta.color + '20', border: `2px solid ${p.meta.color}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 13, fontWeight: 700, color: p.meta.color,
                }}>{p.meta.initial}</div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{p.meta.name}</span>
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
                      background: as.bg, color: as.text, border: `1px solid ${as.border}`,
                      letterSpacing: '0.06em',
                    }}>
                      {p.parsedAction}{p.ticker ? ` · ${p.ticker}` : ''}
                    </span>
                    {p.confidence != null && (
                      <span style={{ fontSize: 11, color: '#4a5568' }}>
                        {Math.round(p.confidence * 100)}% conf
                      </span>
                    )}
                    <span style={{ fontSize: 11, color: '#2d3748', marginLeft: 'auto' }}>
                      {timeAgo(item.created_at)}
                    </span>
                  </div>
                  <div style={{ fontSize: 13, color: '#718096', lineHeight: 1.55, fontStyle: 'italic' }}>
                    "{p.reasoning}"
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
