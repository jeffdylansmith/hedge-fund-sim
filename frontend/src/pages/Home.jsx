import { useState, useEffect } from 'react'
import { LineChart, Line, ResponsiveContainer, Tooltip, XAxis } from 'recharts'

const API = import.meta.env.VITE_API_URL

const TRADER_META = {
  alex:   { name: 'Alex',   style: 'Aggressive Momentum', color: '#FF6B6B', initial: 'A' },
  jordan: { name: 'Jordan', style: 'Conservative Macro',  color: '#4299E1', initial: 'J' },
  casey:  { name: 'Casey',  style: 'Contrarian',          color: '#9F7AEA', initial: 'C' },
}

function timeAgo(ts) {
  const s = (Date.now() - new Date(ts)) / 1000
  if (s < 60)    return `${Math.floor(s)}s ago`
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function buildSparkline(currentValue) {
  const START = 1_000_000
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
  let action     = item.action
  let ticker     = item.ticker
  let reasoning  = item.reasoning ?? ''
  let confidence = item.confidence
  let shares     = null

  if (item.action === 'portfolio_review') {
    try {
      const decisions = JSON.parse(item.reasoning)
      const nonHold = decisions.filter(d => d.action !== 'HOLD')
      if (nonHold.length > 0) {
        const d = nonHold[0]
        action = d.action; ticker = d.ticker
        reasoning = d.reasoning; confidence = d.confidence; shares = d.shares
      } else {
        action = 'HOLD'; ticker = null
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

  return { ...item, parsedAction: action, ticker, reasoning, confidence, shares, meta }
}

function getFilterGroup(parsedAction) {
  if (parsedAction === 'BUY' || parsedAction === 'SELL') return 'trades'
  if (parsedAction === 'trader_reaction')                return 'reactions'
  if (parsedAction === 'daily_summary')                  return 'summaries'
  if (parsedAction === 'risk_veto')                      return 'riskEvents'
  return 'activity'
}

// ── Feed card components ──────────────────────────────────────────────────────

function Avatar({ meta, size = 36 }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: meta.color + '20', border: `2px solid ${meta.color}`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: Math.round(size * 0.36), fontWeight: 700, color: meta.color,
    }}>{meta.initial}</div>
  )
}

function TradeCard({ item }) {
  const [expanded, setExpanded] = useState(false)
  const isBuy      = item.parsedAction === 'BUY'
  const accent     = isBuy ? '#276749' : '#742a2a'
  const badgeBg    = isBuy ? '#0d2318' : '#2a0f0f'
  const badgeText  = isBuy ? '#68d391'  : '#fc8181'
  const LIMIT      = 160
  const long       = (item.reasoning || '').length > LIMIT

  return (
    <div style={{ display: 'flex', gap: 12, borderLeft: `3px solid ${accent}`, paddingLeft: 14 }}>
      <Avatar meta={item.meta} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>{item.meta.name}</span>
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
            background: badgeBg, color: badgeText, border: `1px solid ${accent}`,
            letterSpacing: '0.06em',
          }}>{item.parsedAction}</span>
          {item.ticker && (
            <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{item.ticker}</span>
          )}
          {item.shares > 0 && (
            <span style={{ fontSize: 11, color: '#718096' }}>{item.shares} shares</span>
          )}
          <span style={{ fontSize: 11, color: '#2d3748', marginLeft: 'auto' }}>{timeAgo(item.created_at)}</span>
        </div>

        <div style={{ fontSize: 12, color: '#718096', lineHeight: 1.55, fontStyle: 'italic' }}>
          "{expanded || !long ? item.reasoning : item.reasoning.slice(0, LIMIT) + '…'}"
        </div>
        {long && (
          <button onClick={() => setExpanded(e => !e)} style={{
            fontSize: 11, color: '#4a5568', background: 'none', border: 'none',
            cursor: 'pointer', padding: '3px 0 0', display: 'block',
          }}>{expanded ? '▲ less' : '▼ more'}</button>
        )}

        {item.confidence != null && (
          <div style={{ marginTop: 6 }}>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 10,
              background: '#141a24', color: '#4a5568', border: '1px solid #1e2535',
            }}>{Math.round(item.confidence * 100)}% conf</span>
          </div>
        )}
      </div>
    </div>
  )
}

function ReactionCard({ item }) {
  return (
    <div style={{ display: 'flex', gap: 10 }}>
      <Avatar meta={item.meta} size={30} />
      <div>
        <div style={{
          background: '#1a1f2e', borderRadius: '4px 12px 12px 12px',
          padding: '9px 14px', border: '1px solid #232b3d', maxWidth: 500,
        }}>
          <div style={{ fontSize: 13, color: '#c0cde0', lineHeight: 1.55 }}>
            {item.reasoning}
          </div>
        </div>
        <div style={{ fontSize: 11, color: '#4a5568', marginTop: 3, paddingLeft: 2 }}>
          {item.meta.name}
          {item.ticker && item.ticker !== 'PORTFOLIO' && ` · re: ${item.ticker}`}
          {' · '}{timeAgo(item.created_at)}
        </div>
      </div>
    </div>
  )
}

function RiskVetoCard({ item }) {
  return (
    <div style={{ borderLeft: '3px solid #c05621', paddingLeft: 14, opacity: 0.85 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: '#ed8936' }}>🛑 Risk Veto</span>
        <span style={{ fontSize: 12, color: '#a0aec0' }}>{item.meta.name}</span>
        {item.ticker && <span style={{ fontSize: 11, color: '#718096' }}>· {item.ticker}</span>}
        <span style={{ fontSize: 11, color: '#2d3748', marginLeft: 'auto' }}>{timeAgo(item.created_at)}</span>
      </div>
      <div style={{ fontSize: 12, color: '#a0aec0', lineHeight: 1.5 }}>
        {item.reasoning}
      </div>
    </div>
  )
}

function DailySummaryCard({ item }) {
  const dateStr = new Date(item.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  return (
    <div style={{
      background: '#0f1520', border: '1px solid #2a3548', borderRadius: 8, padding: '14px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: '#90cdf4' }}>📊 EOD Summary</span>
        <span style={{ fontSize: 12, fontWeight: 600, color: item.meta.color }}>— {item.meta.name}</span>
        <span style={{ fontSize: 11, color: '#4a5568', marginLeft: 'auto' }}>{dateStr}</span>
      </div>
      <div style={{ fontSize: 12, color: '#a0aec0', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
        {item.reasoning}
      </div>
    </div>
  )
}

function ActivityCard({ item }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0' }}>
      <span style={{ fontSize: 11, color: '#2d3748', fontWeight: 600, flexShrink: 0 }}>{item.meta.name}</span>
      <span style={{ fontSize: 11, color: '#252f40', flexShrink: 0 }}>{item.parsedAction}</span>
      {item.ticker && (
        <span style={{ fontSize: 11, color: '#252f40', flexShrink: 0 }}>{item.ticker}</span>
      )}
      <span style={{
        fontSize: 11, color: '#252f40', flex: 1,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>{(item.reasoning || '').slice(0, 90)}</span>
      <span style={{ fontSize: 10, color: '#1e2535', flexShrink: 0 }}>{timeAgo(item.created_at)}</span>
    </div>
  )
}

function FeedCard({ item }) {
  const a = item.parsedAction
  if (a === 'BUY' || a === 'SELL')  return <TradeCard item={item} />
  if (a === 'trader_reaction')       return <ReactionCard item={item} />
  if (a === 'daily_summary')         return <DailySummaryCard item={item} />
  if (a === 'risk_veto')             return <RiskVetoCard item={item} />
  return <ActivityCard item={item} />
}

function FilterChip({ label, active, onToggle }) {
  return (
    <button
      onClick={onToggle}
      style={{
        padding: '4px 12px', borderRadius: 20, fontSize: 11, fontWeight: 600,
        cursor: 'pointer', border: '1px solid',
        borderColor: active ? '#4a5568' : '#1e2535',
        background: active ? '#1e2535' : 'transparent',
        color: active ? '#e2e8f0' : '#4a5568',
        transition: 'all 0.15s', letterSpacing: '0.04em',
      }}
    >{label}</button>
  )
}

// ── StatCard ──────────────────────────────────────────────────────────────────

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

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Home() {
  const [summary,   setSummary]   = useState(null)
  const [traders,   setTraders]   = useState([])
  const [feed,      setFeed]      = useState([])
  const [sparkData, setSparkData] = useState([])
  const [filters,   setFilters]   = useState({
    trades:     true,
    reactions:  true,
    summaries:  true,
    riskEvents: true,
    activity:   false,
  })

  function toggleFilter(key) {
    setFilters(f => ({ ...f, [key]: !f[key] }))
  }

  async function fetchFeed() {
    const f = await fetch(`${API}/feed?limit=20`).then(r => r.json())
    setFeed(f)
  }

  async function fetchAll() {
    const [s, t, f] = await Promise.all([
      fetch(`${API}/fund/summary`).then(r => r.json()),
      fetch(`${API}/traders`).then(r => r.json()),
      fetch(`${API}/feed?limit=20`).then(r => r.json()),
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
  const gain   = summary ? summary.total_fund_value - 1_000_000 : 0

  const parsedFeed  = feed.map(parseFeedItem)
  const visibleFeed = parsedFeed.filter(item => filters[getFilterGroup(item.parsedAction)])

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

      {/* ── Live Feed ── */}
      <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 24 }}>

        {/* Header */}
        <div style={{ fontSize: 11, fontWeight: 600, color: '#4a5568', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.07em', display: 'flex', alignItems: 'center', gap: 12 }}>
          Live Feed
          <span style={{ fontWeight: 400, color: '#2d3748' }}>· refreshes every 30s</span>
        </div>

        {/* Filter chips */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
          <FilterChip label="Trades"      active={filters.trades}     onToggle={() => toggleFilter('trades')} />
          <FilterChip label="Reactions"   active={filters.reactions}  onToggle={() => toggleFilter('reactions')} />
          <FilterChip label="Summaries"   active={filters.summaries}  onToggle={() => toggleFilter('summaries')} />
          <FilterChip label="Risk Events" active={filters.riskEvents} onToggle={() => toggleFilter('riskEvents')} />
          <FilterChip label="Activity"    active={filters.activity}   onToggle={() => toggleFilter('activity')} />
        </div>

        {/* Cards */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {visibleFeed.map(item => <FeedCard key={item.id} item={item} />)}
          {visibleFeed.length === 0 && (
            <div style={{ textAlign: 'center', padding: '32px 0', color: '#4a5568', fontSize: 13 }}>
              No items match the current filters.
            </div>
          )}
        </div>

      </div>
    </div>
  )
}
