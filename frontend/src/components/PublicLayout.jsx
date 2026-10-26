import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { usePublicTheme, useSite } from '../lib/site'
import { CopyButton } from '../components/ui'

const LINKS = [
  { to: '/models', label: 'Models' },
  { to: '/docs', label: 'Docs' },
]

export default function PublicLayout() {
  const site = useSite()
  const { user } = useAuth()
  const [theme, toggleTheme] = usePublicTheme()
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    setMenuOpen(false)
    window.scrollTo(0, 0)
  }, [location.pathname])

  // Keep the page title in step with the route (§8 SEO).
  useEffect(() => {
    const titles = {
      '/': site.tagline,
      '/models': 'Models',
      '/docs': 'Documentation',
      '/request-access': 'Request access',
      '/terms': 'Terms of Service',
      '/privacy': 'Privacy Policy',
    }
    const name = titles[location.pathname]
    document.title = name ? `${name} · ${site.app_name}` : site.app_name
  }, [location.pathname, site.app_name, site.tagline])

  const links = site.status_page_url
    ? [...LINKS, { to: site.status_page_url, label: 'Status', external: true }]
    : LINKS

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-30 border-b border-border bg-bg/90 backdrop-blur-sm">
        <div className="mx-auto w-full max-w-content px-6">
          <div className="flex h-14 items-center gap-3">
            <Link to="/" className="flex items-center gap-2 shrink min-w-0" aria-label={`${site.app_name} home`}>
              <span className="h-7 w-7 rounded-lg bg-accent text-accent-fg grid place-items-center text-sm font-semibold">
                {site.app_name.slice(0, 1).toUpperCase()}
              </span>
              <span className="font-semibold tracking-tight truncate">{site.app_name}</span>
            </Link>

            <nav className="hidden md:flex items-center gap-1 ml-4" aria-label="Main">
              {links.map((link) =>
                link.external ? (
                  <a
                    key={link.to}
                    href={link.to}
                    className="px-2.5 h-8 inline-flex items-center rounded-md text-sm text-muted hover:text-fg hover:bg-surface"
                  >
                    {link.label}
                  </a>
                ) : (
                  <NavLink
                    key={link.to}
                    to={link.to}
                    className={({ isActive }) =>
                      `px-2.5 h-8 inline-flex items-center rounded-md text-sm transition-colors ${
                        isActive ? 'text-accent font-medium bg-accent-soft' : 'text-muted hover:text-fg hover:bg-surface'
                      }`
                    }
                  >
                    {link.label}
                  </NavLink>
                ),
              )}
            </nav>

            <div className="ml-auto flex items-center gap-1.5 sm:gap-2 shrink-0">
              <button
                onClick={toggleTheme}
                className="hidden sm:inline-flex h-9 w-9 items-center justify-center rounded-control text-muted hover:text-fg hover:bg-surface"
                aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              >
                {theme === 'dark' ? '☀' : '☾'}
              </button>
              {user ? (
                <Link to="/console" className="btn-primary btn-sm">
                  Open console
                </Link>
              ) : (
                <>
                  <Link to="/login" className="btn-secondary btn-sm hidden sm:inline-flex">
                    Sign in
                  </Link>
                  <Link to="/request-access" className="btn-primary btn-sm px-2.5 whitespace-nowrap">
                    Request access
                  </Link>
                </>
              )}
              <button
                className="btn-ghost btn-sm px-1.5 md:hidden"
                onClick={() => setMenuOpen((open) => !open)}
                aria-expanded={menuOpen}
                aria-label="Toggle menu"
              >
                ☰
              </button>
            </div>
          </div>

          {menuOpen && (
            <nav className="md:hidden pb-3 flex flex-col gap-1" aria-label="Mobile">
              {links.map((link) => (
                <Link
                  key={link.to}
                  to={link.to}
                  className="px-2 h-9 inline-flex items-center rounded-md text-sm text-muted hover:text-fg hover:bg-surface"
                >
                  {link.label}
                </Link>
              ))}
              {!user && (
                <Link to="/login" className="px-2 h-9 inline-flex items-center rounded-md text-sm text-muted hover:text-fg hover:bg-surface">
                  Sign in
                </Link>
              )}
            </nav>
          )}
        </div>
      </header>

      <main className="flex-1">
        <Outlet />
      </main>

      <PublicFooter site={site} />
    </div>
  )
}

function PublicFooter({ site }) {
  const year = new Date().getFullYear()
  return (
    <footer className="border-t border-border mt-24">
      <div className="mx-auto w-full max-w-content px-6 py-12">
        <div className="grid gap-8 sm:grid-cols-3">
          <div>
            <div className="text-sm font-semibold mb-3">Product</div>
            <ul className="space-y-2 text-sm text-muted">
              <li><Link to="/models" className="hover:text-fg">Models</Link></li>
              <li><Link to="/docs" className="hover:text-fg">Docs</Link></li>
              {site.status_page_url && (
                <li><a href={site.status_page_url} className="hover:text-fg">Status</a></li>
              )}
            </ul>
          </div>
          <div>
            <div className="text-sm font-semibold mb-3">Legal</div>
            <ul className="space-y-2 text-sm text-muted">
              <li><Link to="/terms" className="hover:text-fg">Terms of Service</Link></li>
              <li><Link to="/privacy" className="hover:text-fg">Privacy Policy</Link></li>
            </ul>
          </div>
          <div>
            <div className="text-sm font-semibold mb-3">Contact</div>
            <ul className="space-y-2 text-sm text-muted">
              <li>
                <a href={`mailto:${site.support_email}`} className="hover:text-fg break-all">
                  {site.support_email}
                </a>
              </li>
              <li><Link to="/request-access" className="hover:text-fg">Request access</Link></li>
              <li><Link to="/login" className="hover:text-fg">Sign in</Link></li>
            </ul>
          </div>
        </div>
        <p className="mt-10 text-xs text-muted">
          © {year} {site.app_name}. Available by invitation only.
        </p>
      </div>
    </footer>
  )
}

/** Shared section wrapper: consistent vertical rhythm across public pages. */
export function Section({ children, className = '' }) {
  return (
    <section className={`mx-auto w-full max-w-content px-6 ${className}`}>
      {children}
    </section>
  )
}

/** A small copyable code block used on the homepage and in docs. */
export function CodeBlock({ code, language = 'bash', showCopy = true }) {
  return (
    <div className="relative rounded-card border border-border bg-surface overflow-hidden">
      {showCopy && (
        <div className="absolute right-2 top-2 z-10">
          <CopyButton value={code} label="Copy" />
        </div>
      )}
      <pre className="p-4 overflow-x-auto">
        <code className="mono text-xs whitespace-pre" data-language={language}>
          {code}
        </code>
      </pre>
    </div>
  )
}
