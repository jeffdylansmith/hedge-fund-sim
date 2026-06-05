import { useState, useEffect, useRef } from 'react'

const API = import.meta.env.VITE_API_URL

const TRADERS = {
  alex:   { name: 'Alex',   color: '#e07060', initial: 'A' },
  jordan: { name: 'Jordan', color: '#6080e0', initial: 'J' },
  casey:  { name: 'Casey',  color: '#9060e0', initial: 'C' },
}

function fmt(n) {
  return Number(n).toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function TypingIndicator({ color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 16 }}>
      <div style={{
        width: 32, height: 32, borderRadius: '50%', background: color,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13, fontWeight: 700, color: '#fff', flexShrink: 0,
      }}>
        {TRADERS[Object.keys(TRADERS).find(k => TRADERS[k].color === color)]?.initial ?? '?'}
      </div>
      <div style={{
        background: '#1a2035', borderRadius: '12px 12px 12px 0',
        padding: '12px 16px', display: 'flex', gap: 5, alignItems: 'center',
      }}>
        {[0, 1, 2].map(i => (
          <span key={i} style={{
            width: 7, height: 7, borderRadius: '50%', background: '#4a5568',
            display: 'inline-block',
            animation: 'chatBounce 1.2s ease-in-out infinite',
            animationDelay: `${i * 0.2}s`,
          }} />
        ))}
      </div>
    </div>
  )
}

export default function Chat() {
  const [selectedTrader, setSelectedTrader] = useState('alex')
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [positions, setPositions] = useState([])
  const [traderData, setTraderData] = useState(null)
  const [positionsLoading, setPositionsLoading] = useState(false)
  const bottomRef = useRef(null)

  const trader = TRADERS[selectedTrader]

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    setPositionsLoading(true)
    Promise.all([
      fetch(`${API}/traders/${selectedTrader}/positions`).then(r => r.json()),
      fetch(`${API}/traders`).then(r => r.json()),
    ])
      .then(([posData, tradersData]) => {
        setPositions(posData.positions ?? [])
        const me = Array.isArray(tradersData)
          ? tradersData.find(t => t.trader_id === selectedTrader)
          : null
        setTraderData(me)
      })
      .catch(() => {})
      .finally(() => setPositionsLoading(false))
  }, [selectedTrader])

  function selectTrader(id) {
    setSelectedTrader(id)
    setMessages([])
  }

  async function send() {
    const text = input.trim()
    if (!text || loading) return

    const history = messages.map(m => ({ role: m.role, content: m.content }))
    setMessages(prev => [...prev, { role: 'user', content: text }])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch(`${API}/chat/${selectedTrader}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history }),
      })
      const data = await res.json()
      setMessages(prev => [...prev, { role: 'assistant', content: data.reply }])
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: "Sorry, I'm having trouble responding right now.",
      }])
    } finally {
      setLoading(false)
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <>
      <style>{`
        @keyframes chatBounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-6px); opacity: 1; }
        }
      `}</style>

      {/* Trader selector bar */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
        {Object.entries(TRADERS).map(([id, t]) => {
          const active = id === selectedTrader
          return (
            <button
              key={id}
              onClick={() => selectTrader(id)}
              style={{
                padding: '8px 24px',
                borderRadius: 8,
                border: `2px solid ${t.color}`,
                background: active ? t.color : 'transparent',
                color: active ? '#fff' : t.color,
                fontWeight: 600,
                fontSize: 14,
                cursor: 'pointer',
                transition: 'all 0.15s',
              }}
            >
              {t.name}
            </button>
          )
        })}
      </div>

      {/* Two-column layout */}
      <div style={{ display: 'flex', gap: 20, height: 'calc(100vh - 180px)' }}>

        {/* LEFT — Positions */}
        <div style={{
          width: '35%', flexShrink: 0,
          background: '#161b27', borderRadius: 12,
          border: '1px solid #1e2535',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}>
          <div style={{
            padding: '16px 20px',
            borderBottom: '1px solid #1e2535',
            fontWeight: 600, fontSize: 15, color: '#e2e8f0',
          }}>
            {trader.name}'s Positions
          </div>

          <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>
            {positionsLoading ? (
              <div style={{ color: '#718096', textAlign: 'center', paddingTop: 40 }}>Loading...</div>
            ) : positions.length === 0 ? (
              <div style={{ color: '#718096', textAlign: 'center', paddingTop: 40 }}>
                <div style={{ marginBottom: 8 }}>No open positions</div>
                {traderData && (
                  <div style={{ color: '#4a5568', fontSize: 13 }}>
                    Cash: {fmt(traderData.cash)}
                  </div>
                )}
              </div>
            ) : (
              <>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ color: '#718096', textAlign: 'left' }}>
                      <th style={{ paddingBottom: 10, fontWeight: 500 }}>Ticker</th>
                      <th style={{ paddingBottom: 10, fontWeight: 500, textAlign: 'right' }}>Shares</th>
                      <th style={{ paddingBottom: 10, fontWeight: 500, textAlign: 'right' }}>P&L%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map(p => (
                      <tr key={p.ticker} style={{ borderTop: '1px solid #1e2535' }}>
                        <td style={{ padding: '9px 0', color: '#e2e8f0', fontWeight: 600 }}>{p.ticker}</td>
                        <td style={{ padding: '9px 0', color: '#a0aec0', textAlign: 'right' }}>{p.shares}</td>
                        <td style={{
                          padding: '9px 0', textAlign: 'right', fontWeight: 600,
                          color: p.unrealized_pnl_pct >= 0 ? '#68d391' : '#fc8181',
                        }}>
                          {p.unrealized_pnl_pct >= 0 ? '+' : ''}{p.unrealized_pnl_pct?.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {traderData && (
                  <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #1e2535', color: '#718096', fontSize: 13 }}>
                    <div>Cash: <span style={{ color: '#a0aec0' }}>{fmt(traderData.cash)}</span></div>
                    <div style={{ marginTop: 4 }}>Total: <span style={{ color: '#a0aec0' }}>{fmt(traderData.total_value)}</span></div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* RIGHT — Chat */}
        <div style={{
          flex: 1,
          background: '#161b27', borderRadius: 12,
          border: '1px solid #1e2535',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {/* Messages */}
          <div style={{ flex: 1, overflow: 'auto', padding: '20px 20px 8px' }}>
            {messages.length === 0 && !loading ? (
              <div style={{
                color: '#4a5568', textAlign: 'center',
                paddingTop: 80, fontSize: 15,
              }}>
                {trader.name} is ready to talk trading. Ask anything.
              </div>
            ) : (
              <>
                {messages.map((m, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      flexDirection: m.role === 'user' ? 'row-reverse' : 'row',
                      alignItems: 'flex-end',
                      gap: 10,
                      marginBottom: 16,
                    }}
                  >
                    {m.role === 'assistant' && (
                      <div style={{
                        width: 32, height: 32, borderRadius: '50%',
                        background: trader.color, flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 13, fontWeight: 700, color: '#fff',
                      }}>
                        {trader.initial}
                      </div>
                    )}
                    <div style={{
                      maxWidth: '72%',
                      padding: '10px 14px',
                      borderRadius: m.role === 'user'
                        ? '12px 12px 0 12px'
                        : '12px 12px 12px 0',
                      background: m.role === 'user' ? trader.color : '#1a2035',
                      color: '#e2e8f0',
                      fontSize: 14,
                      lineHeight: 1.5,
                    }}>
                      {m.content}
                    </div>
                  </div>
                ))}
                {loading && <TypingIndicator color={trader.color} />}
              </>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input bar */}
          <div style={{
            padding: '12px 16px',
            borderTop: '1px solid #1e2535',
            display: 'flex', gap: 10,
          }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={loading}
              placeholder={`Ask ${trader.name} anything...`}
              style={{
                flex: 1,
                background: '#0f1117',
                border: '1px solid #1e2535',
                borderRadius: 8,
                padding: '10px 14px',
                color: '#e2e8f0',
                fontSize: 14,
                outline: 'none',
                opacity: loading ? 0.6 : 1,
              }}
            />
            <button
              onClick={send}
              disabled={loading || !input.trim()}
              style={{
                background: trader.color,
                border: 'none',
                borderRadius: 8,
                padding: '10px 20px',
                color: '#fff',
                fontWeight: 600,
                fontSize: 14,
                cursor: loading || !input.trim() ? 'not-allowed' : 'pointer',
                opacity: loading || !input.trim() ? 0.5 : 1,
                transition: 'opacity 0.15s',
              }}
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
