import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { api } from '../lib/api'
import { Dot } from './ui'

const NAV = [
  { to: '/console', label: 'Dashboard', end: true, icon: 'grid' },
  { to: '/console/keys', label: 'API Keys', icon: 'key' },
  { to: '/console/providers', label: 'Providers', owner: true, icon: 'plug' },
  { to: '/console/activity', label: 'Activity', icon: 'list' },
  { to: '/console/usage', label: 'Usage & Cost', icon: 'chart' },
  { to: '/console/models', label: 'Models', owner: true, icon: 'cpu' },
  { to: '/console/routing', label: 'Routing', owner: true, icon: 'route' },
  { to: '/console/playground', label: 'Playground', icon: 'play' },
  { to: '/console/partners', label: 'Partners', owner: true, icon: 'users' },
  { to: '/console/access-requests', label: 'Access Requests', owner: true, icon: 'inbox', badge: 'pending' },
  { to: '/console/maintenance', label: 'Maintenance', owner: true, icon: 'wrench', badge: 'maint' },
  { to: '/console/settings', label: 'Settings', owner: true, icon: 'settings' },
  { to: '/console/docs', label: 'Docs', icon: 'book' },
]

function Icon({ name }) {
  const paths = {
    grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    key: 'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3',
    plug: 'M12 22v-5M9 8V2M15 8V2M6 8h12v3a6 6 0 0 1-12 0z',
    list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
    chart: 'M3 3v18h18M7 15l4-4 3 3 5-6',
    cpu: 'M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3M6 6h12v12H6z',
    route: 'M6 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM9 16h4a4 4 0 0 0 0-8H9',
    play: 'M5 3l14 9-14 9V3z',
    users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
    settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
    book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
    inbox: 'M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z',
    wrench: 'M14.7 6.3a4 4 0 1 0 5 5L21 21l-3 0-9.3-9.3a4 4 0 0 1-5-5L6 9l3-3 5.7.3z',
    menu: 'M3 12h18M3 6h18M3 18h18',
    search: 'M21 21l-4.35-4.35M17 11a6 6 0 1 1-12 0 6 6 0 0 1 12 0z',
    sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
    moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  }
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <path d={paths[name] || paths.grid} />
    </svg>
  )
}

function useTheme() {
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem('gw-theme')
    if (stored) return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem('gw-theme', theme)
  }, [theme])

  return [theme, setTheme]
}

export default function Layout() {
  const { user, logout, isOwner } = useAuth()
  const [theme, setTheme] = useTheme()
  const [open, setOpen] = useState(false)
  const [health, setHealth] = useState(null)
  const [pendingCount, setPendingCount] = useState(0)
  const [legalReviewed, setLegalReviewed] = useState(true)
  const [maintState, setMaintState] = useState({ state: 'normal' })
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const data = await api.get('/health')
        if (alive) setHealth(data)
      } catch {
        if (alive) setHealth(null)
      }
    }
    load()
    const timer = window.setInterval(load, 30000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  const items = NAV.filter((item) => !item.owner || isOwner)
  const env = health?.status || 'unknown'
  const envTone = env === 'ok' ? 'success' : env === 'degraded' ? 'warning' : 'danger'

  // Pending access requests, and whether the legal pages are still placeholders.
  useEffect(() => {
    if (!isOwner) return undefined
    let alive = true
    api
      .get('/admin/access-requests?status=pending')
      .then((data) => {
        if (alive) setPendingCount(data.pending_count || 0)
      })
      .catch(() => {})
    api
      .get('/public/config')
      .then((data) => {
        if (alive) setLegalReviewed(data.legal_reviewed !== false)
      })
      .catch(() => {})
    api
      .get('/public/status')
      .then((data) => {
        if (alive) setMaintState(data)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [isOwner, location.pathname])

  return (
    <div className="min-h-full flex">
      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-60 border-r border-border bg-bg flex flex-col
          transition-transform lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <div className="h-14 flex items-center gap-2 px-4 border-b border-border bg-bg">
          <div className="h-7 w-7 rounded-lg bg-accent text-accent-fg grid place-items-center text-sm font-semibold">
            G
          </div>
          <span className="font-semibold tracking-tight">Gateway</span>
          <span className="ml-auto badge-neutral mono text-[10px]">v1</span>
        </div>

        <nav className="flex-1 overflow-y-auto p-2">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 h-9 px-2.5 rounded-md text-sm transition-colors
                 ${
                   isActive
                     ? 'bg-accent-soft text-accent font-medium'
                     : 'text-muted hover:text-fg hover:bg-surface'
                 }`
              }
            >
              <Icon name={item.icon} />
              {item.label}
              {item.badge === 'pending' && pendingCount > 0 && (
                <span className="ml-auto badge-warning mono text-[10px] h-4 px-1.5">
                  {pendingCount}
                </span>
              )}
              {item.badge === 'maint' && maintState.state !== 'normal' && (
                <span
                  className={`ml-auto h-2 w-2 rounded-full ${
                    maintState.state === 'maintenance' ? 'bg-danger' : 'bg-warning'
                  }`}
                  aria-label={maintState.state === 'maintenance' ? 'Maintenance active' : 'Degraded'}
                />
              )}
            </NavLink>
          ))}
        </nav>

        <div className="p-2 border-t border-border">
          <div className="px-2.5 py-2 flex items-center gap-2 text-xs text-muted">
            <Dot tone={envTone} pulse={env === 'ok'} />
            <span className="capitalize">{env}</span>
            <span className="ml-auto mono text-[10px]">
              {health?.shared_state === 'redis' ? 'redis' : 'memory'}
            </span>
          </div>
          <div className="flex items-center gap-2 px-1 py-1">
            <div className="h-7 w-7 rounded-full bg-accent-soft text-accent grid place-items-center text-xs font-medium shrink-0">
              {(user?.email || '?')[0].toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs truncate">{user?.email}</div>
              <div className="text-[10px] text-muted capitalize">{user?.role}</div>
            </div>
            <button
              className="btn-ghost btn-sm"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label="Toggle theme"
              title="Toggle theme"
            >
              <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            </button>
          </div>
          <button
            className="btn-ghost btn-sm w-full justify-start text-muted mt-1"
            onClick={async () => {
              await logout()
              navigate('/login')
            }}
          >
            Sign out
          </button>
        </div>
      </aside>

      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Content */}
      <div className="flex-1 lg:ml-60 min-w-0 flex flex-col">
        <header className="h-14 border-b border-border flex items-center gap-3 px-4 sticky top-0 bg-bg/90 backdrop-blur-sm z-20">
          <button
            className="btn-ghost btn-sm lg:hidden"
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
          >
            <Icon name="menu" />
          </button>
          <SearchBox />
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden sm:inline-flex items-center gap-1.5 text-xs text-muted">
              <Dot tone={envTone} />
              {health?.usable_providers ?? 0} provider{(health?.usable_providers ?? 0) === 1 ? '' : 's'} usable
            </span>
          </div>
        </header>

        <main className="flex-1 w-full max-w-[1200px] mx-auto p-4 sm:p-6 overflow-x-hidden">
          {maintState.state !== 'normal' && (
            <a
              href="/console/maintenance"
              className={`mb-4 card px-4 py-3 text-sm flex items-center gap-3 ${
                maintState.state === 'maintenance' ? 'border-danger/50' : 'border-warning/40'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full shrink-0 ${
                  maintState.state === 'maintenance' ? 'bg-danger' : 'bg-warning'
                }`}
                aria-hidden="true"
              />
              <span className="flex-1">
                {maintState.state === 'maintenance'
                  ? maintState.message || 'Maintenance is active: all API calls are blocked.'
                  : 'Some services are running with restrictions.'}
              </span>
              <span className="text-xs text-muted">Review →</span>
            </a>
          )}
          {isOwner && !legalReviewed && (
            <div className="mb-4 card border-warning/40 px-4 py-3 text-sm flex items-start gap-3">
              <span className="text-warning shrink-0" aria-hidden="true">⚠</span>
              <span className="flex-1">
                Legal pages still contain placeholder text. Have them reviewed and set{' '}
                <code className="mono text-xs">LEGAL_REVIEWED=true</code> before inviting partners.
              </span>
              <a href="/terms" target="_blank" rel="noreferrer" className="btn-secondary btn-sm shrink-0">
                View
              </a>
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function SearchBox() {
  const navigate = useNavigate()
  const [value, setValue] = useState('')

  return (
    <form
      className="relative w-full max-w-xs"
      onSubmit={(event) => {
        event.preventDefault()
        if (value.trim()) navigate(`/activity?search=${encodeURIComponent(value.trim())}`)
      }}
    >
      <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted">
        <Icon name="search" />
      </span>
      <input
        className="input pl-8 h-8 text-xs"
        placeholder="Search request id…"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-label="Search requests by id"
      />
    </form>
  )
}
