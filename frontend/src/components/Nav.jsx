import { NavLink } from 'react-router-dom'

const links = [
  { to: '/', label: 'Feed' },
  { to: '/leaderboard', label: 'Leaderboard' },
  { to: '/decisions', label: 'Decisions' },
  { to: '/team', label: 'Team' },
  { to: '/chat', label: 'Chat' },
]

export default function Nav() {
  return (
    <nav style={{
      background: '#161b27',
      borderBottom: '1px solid #1e2535',
      padding: '0 24px',
      display: 'flex',
      alignItems: 'center',
      height: 56,
      gap: 32,
    }}>
      <span style={{ fontWeight: 700, fontSize: 16, color: '#fff', letterSpacing: '-0.3px', marginRight: 8 }}>
        Meridian Capital
      </span>
      <span style={{ color: '#4a5568', fontSize: 12, marginRight: 16 }}>Simulated Fund</span>
      <div style={{ display: 'flex', gap: 4 }}>
        {links.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            style={({ isActive }) => ({
              padding: '6px 14px',
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 500,
              color: isActive ? '#fff' : '#718096',
              background: isActive ? '#1e2535' : 'transparent',
              transition: 'all 0.15s',
            })}
          >
            {label}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
