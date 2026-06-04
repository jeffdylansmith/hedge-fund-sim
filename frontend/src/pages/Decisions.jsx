import { useState, useEffect, useCallback } from 'react'

const API = import.meta.env.VITE_API_URL

const TRADER_META = {
  alex:   { name: 'Alex',   color: '#FF6B6B', initial: 'A' },
  jordan: { name: 'Jordan', color: '#4299E1', initial: 'J' },
  casey:  { name: 'Casey',  color: '#9F7AEA', initial: 'C' },
}

const ACTION_STYLE = {
  BUY:      { bg: '#0d2318', text: '#68d391', border: '#276749' },
  SELL:     { bg: '#2a0f0f', text: '#fc8181', border: '#742a2a' },
  HOLD:     { bg: '#0f1a2e', text: '#90cdf4', border: '#2a4365' },
  ANALYSIS: { bg: '#1a1030', text: '#b794f4', border: '#44337a' },
  REVIEW:   { bg: '#0f1a2e', text: '#90cdf4', border: '#2a4365' },
  PENDING:  { bg: '#2a2010', text: '#f6e05e', border: '#744210' },
}

function timeAgo(ts) {
  const s = (Date.now() - new Date(ts)) / 1000
  if (s < 60)    return `${Math.floor(s)}s ago`
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function parseFeedItem(item) {
  const meta = TRADER_META[item.trader_id] ?? { name: item.trader_id, color: '#718096', initial: '?' }
  let action = item.action
  let ticker = item.ticker
  let shortReasoning = item.reasoning ?? ''
  let confidence = item.confidence

  if (item.action === 'portfolio_review') {
    try {
      const decisions = JSON.parse(item.reasoning)
      const nonHold = decisions.filter(d => d.action !== 'HOLD')
      if (nonHold.length > 0) {
        const d = nonHold[0]
        action = d.action; ticker = d.ticker
        shortReasoning = d.reasoning; confidence = d.confidence
      } else {
        action = 'HOLD'; ticker = null
        shortReasoning = 'All positions held — waiting for cleaner signals.'
        confidence = decisions[0]?.confidence ?? null
      }
    } catch { action = 'REVIEW' }
  } else if (item.action === 'analyze') {
    action = 'ANALYSIS'
    shortReasoning = (item.reasoning ?? '').split(/\.\s+/)[0].trimEnd().replace(/\.$/, '') + '.'
  }

  return { ...item, parsedAction: action, ticker, shortReasoning, confidence, meta }
}

function Avatar({ meta, size = 36 }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: meta.color + '20', border: `2px solid ${meta.color}`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: size * 0.38, fontWeight: 700, color: meta.color,
    }}>{meta.initial}</div>
  )
}

function ActionTag({ action, ticker }) {
  const s = ACTION_STYLE[action] ?? ACTION_STYLE.REVIEW
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
      background: s.bg, color: s.text, border: `1px solid ${s.border}`,
      letterSpacing: '0.06em', whiteSpace: 'nowrap',
    }}>
      {action}{ticker ? ` · ${ticker}` : ''}
    </span>
  )
}

export default function Decisions() {
  const [feed,         setFeed]         = useState([])
  const [expanded,     setExpanded]     = useState(new Set())
  const [traderFilter, setTraderFilter] = useState('all')
  const [actionFilter, setActionFilter] = useState('all')

  const fetchData = useCallback(async () => {
    const f = await fetch(`${API}/feed?limit=50`).then(r => r.json())
    setFeed(f)
  }, [])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 60_000)
    return () => clearInterval(id)
  }, [fetchData])

  function toggleExpand(id) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const parsedFeed = feed.map(parseFeedItem)

  const filtered = parsedFeed.filter(item => {
    if (traderFilter !== 'all' && item.trader_id !== traderFilter) return false
    if (actionFilter !== 'all' && item.parsedAction !== actionFilter) return false
    return true
  })

  const filterBtn = (label, active, onClick) => (
    <button
      onClick={onClick}
      style={{
        padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: 'pointer',
        border: '1px solid',
        borderColor: active ? '#4a5568' : '#1e2535',
        background: active ? '#1e2535' : 'transparent',
        color: active ? '#e2e8f0' : '#4a5568',
        transition: 'all 0.15s',
      }}
    >{label}</button>
  )

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Decisions</h1>
        <p style={{ fontSize: 13, color: '#4a5568' }}>Full agent decision log</p>
      </div>

      {/* ── Filters ── */}
      <div style={{ display: 'flex', gap: 24, marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: '#4a5568', marginRight: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Trader</span>
          {filterBtn('All', traderFilter === 'all', () => setTraderFilter('all'))}
          {['alex', 'jordan', 'casey'].map(id =>
            filterBtn(TRADER_META[id].name, traderFilter === id, () => setTraderFilter(id))
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: '#4a5568', marginRight: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Action</span>
          {filterBtn('All', actionFilter === 'all', () => setActionFilter('all'))}
          {['BUY', 'SELL', 'HOLD', 'ANALYSIS'].map(a =>
            filterBtn(a, actionFilter === a, () => setActionFilter(a))
          )}
        </div>
      </div>

      {/* ── Feed ── */}
      <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 24 }}>
        <div style={{ fontSize: 11, color: '#4a5568', marginBottom: 16 }}>
          {filtered.length} decision{filtered.length !== 1 ? 's' : ''}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {filtered.map((item, idx) => {
            const as = ACTION_STYLE[item.parsedAction] ?? ACTION_STYLE.REVIEW
            const isOpen = expanded.has(item.id)
            return (
              <div
                key={item.id}
                style={{
                  padding: '14px 0',
                  borderBottom: idx < filtered.length - 1 ? '1px solid #1a2030' : 'none',
                }}
              >
                <div style={{ display: 'flex', gap: 14 }}>
                  <Avatar meta={item.meta} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{item.meta.name}</span>
                      <ActionTag action={item.parsedAction} ticker={item.ticker} />
                      {item.confidence != null && (
                        <span style={{ fontSize: 11, color: '#4a5568' }}>{Math.round(item.confidence * 100)}% conf</span>
                      )}
                      <span style={{ fontSize: 11, color: '#2d3748', marginLeft: 'auto' }}>{timeAgo(item.created_at)}</span>
                    </div>
                    <div style={{ fontSize: 13, color: '#718096', lineHeight: 1.55, fontStyle: 'italic', marginBottom: 6 }}>
                      "{item.shortReasoning}"
                    </div>
                    {/* Full reasoning toggle */}
                    {item.reasoning !== item.shortReasoning && (
                      <button
                        onClick={() => toggleExpand(item.id)}
                        style={{
                          fontSize: 11, color: '#4a5568', background: 'none', border: 'none',
                          cursor: 'pointer', padding: 0, marginTop: 4,
                        }}
                      >
                        {isOpen ? '▲ collapse' : '▼ full reasoning'}
                      </button>
                    )}
                    {isOpen && (
                      <div style={{
                        marginTop: 10, padding: 14, background: '#0f1117',
                        borderRadius: 8, fontSize: 12, color: '#a0aec0', lineHeight: 1.6,
                        border: '1px solid #1a2030', whiteSpace: 'pre-wrap',
                      }}>
                        {item.reasoning}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
          {filtered.length === 0 && (
            <div style={{ textAlign: 'center', padding: '40px 0', color: '#4a5568', fontSize: 13 }}>
              No decisions match the current filters.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
