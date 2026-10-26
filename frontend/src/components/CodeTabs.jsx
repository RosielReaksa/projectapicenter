import { useEffect, useRef, useState } from 'react'
import { copyText } from '../lib/format'

/**
 * Code tabs with explicit labels (STYLE_UPDATE.md B2).
 *
 * Labels are declared, never derived from the key or CSS `capitalize`, which
 * turned "curl" into "Curl" and "javascript" into "Javascript".
 */
export const CODE_LANGUAGES = {
  curl: 'curl',
  python: 'Python',
  javascript: 'JavaScript',
  retry: 'Retry',
}

export function CodeTabs({ snippets, defaultTab, onTabChange }) {
  const keys = Object.keys(snippets)
  const [tab, setTab] = useState(defaultTab || keys[0])

  useEffect(() => {
    if (onTabChange) onTabChange(tab)
  }, [tab, onTabChange])

  return (
    <div className="rounded-card border border-border overflow-hidden bg-surface">
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        {keys.map((key) => {
          const active = tab === key
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              aria-pressed={active}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${
                active
                  ? 'bg-surface-raised text-fg font-medium'
                  : 'text-muted hover:text-fg'
              }`}
            >
              {CODE_LANGUAGES[key] || key}
            </button>
          )
        })}
        <div className="ml-auto">
          <CopyButton value={snippets[tab]} />
        </div>
      </div>
      <pre className="p-4 overflow-x-auto">
        <code className="mono whitespace-pre">{snippets[tab]}</code>
      </pre>
    </div>
  )
}

/** Copy button that shows a "Copied" state for 2 seconds (§7). */
export function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  const handle = async () => {
    const ok = await copyText(value)
    if (!ok) return
    setCopied(true)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      type="button"
      onClick={handle}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs text-muted hover:text-fg transition-colors"
      aria-live="polite"
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        {copied ? (
          <path d="M20 6 9 17l-5-5" />
        ) : (
          <>
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </>
        )}
      </svg>
      {copied ? 'Copied' : label}
    </button>
  )
}
