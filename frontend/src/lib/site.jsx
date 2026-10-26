import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from './api'

const SiteContext = createContext(null)

const FALLBACK = {
  app_name: 'Private LLM Gateway',
  tagline: 'One API for fast, reliable AI models.',
  base_url: '',
  api_base_url: '',
  support_email: 'support@example.com',
  status_page_url: null,
  public_pricing: false,
  legal_reviewed: false,
}

/** Public site config: product name, base URL, support email, feature flags. */
export function SiteProvider({ children }) {
  const [config, setConfig] = useState(FALLBACK)

  useEffect(() => {
    let alive = true
    api
      .get('/public/config')
      .then((data) => {
        if (alive) setConfig({ ...FALLBACK, ...data })
      })
      .catch(() => {
        // The public site still renders with defaults if config fails.
      })
    return () => {
      alive = false
    }
  }, [])

  const value = useMemo(() => config, [config])
  return <SiteContext.Provider value={value}>{children}</SiteContext.Provider>
}

export function useSite() {
  const context = useContext(SiteContext)
  if (!context) throw new Error('useSite must be used inside SiteProvider')
  return context
}

/** Theme for public pages: system preference first, then the stored choice. */
export function usePublicTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      const stored = localStorage.getItem('gw-theme')
      if (stored === 'dark' || stored === 'light') return stored
    } catch {
      /* private mode: fall through to the system preference */
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      localStorage.setItem('gw-theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'))
  }, [])

  return [theme, toggle]
}
