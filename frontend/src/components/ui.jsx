import { useEffect } from 'react'
import { copyText, healthLabel, healthTone, money, ms, relative } from '../lib/format'

export function Badge({ tone = 'neutral', children, className = '' }) {
  const tones = {
    neutral: 'badge-neutral',
    success: 'badge-success',
    warning: 'badge-warning',
    danger: 'badge-danger',
    accent: 'badge-accent',
  }
  return <span className={`${tones[tone] || tones.neutral} ${className}`}>{children}</span>
}

export function Dot({ tone = 'neutral', pulse = false }) {
  const colors = {
    neutral: 'bg-muted',
    success: 'bg-success',
    warning: 'bg-warning',
    danger: 'bg-danger',
    accent: 'bg-accent',
  }
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0" aria-hidden="true">
      {pulse && (
        <span
          className={`absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping ${colors[tone]}`}
        />
      )}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${colors[tone]}`} />
    </span>
  )
}

export function StatusDot({ status }) {
  return (
    <span className="inline-flex items-center gap-2">
      <Dot tone={healthTone(status)} pulse={status === 'healthy'} />
      <span className="text-xs">{healthLabel(status)}</span>
    </span>
  )
}

export function Card({ children, className = '', title, subtitle, action, bodyClass = 'p-4' }) {
  return (
    <section className={`card min-w-0 ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 px-4 py-3 border-b border-border">
          <div className="min-w-0">
            {title && <h2 className="section-title">{title}</h2>}
            {subtitle && <p className="text-xs text-muted mt-0.5">{subtitle}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={`min-w-0 ${bodyClass}`}>{children}</div>
    </section>
  )
}

export function StatCard({ label, value, hint, tone = 'neutral', mono = true }) {
  const toneClass =
    tone === 'danger' ? 'text-danger' : tone === 'warning' ? 'text-warning' : 'text-fg'
  return (
    <div className="card p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-2 text-[28px] leading-none tracking-tight ${mono ? 'mono' : 'serif'} ${toneClass}`}>
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  )
}

export function EmptyState({ title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="text-xs text-muted mt-1 max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-muted text-sm">
      <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
        <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      {label}
    </div>
  )
}

export function ErrorBox({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="card border-danger/40 p-4">
      <div className="flex items-start gap-3">
        <div className="text-danger text-sm font-medium">Something went wrong</div>
        <div className="flex-1 text-sm text-muted">{error.message || String(error)}</div>
        {onRetry && (
          <button className="btn-secondary btn-sm" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
      {error.requestId && (
        <p className="mt-2 text-xs text-muted mono">request {error.requestId}</p>
      )}
    </div>
  )
}

export function Modal({ open, onClose, title, description, children, footer, width = 'max-w-lg' }) {
  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:items-center">
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`relative w-full ${width} card-raised animate-in my-8`}
      >
        <header className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border">
          <div>
            <h2 className="text-base font-semibold">{title}</h2>
            {description && <p className="text-xs text-muted mt-1">{description}</p>}
          </div>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </header>
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border bg-surface rounded-b-card">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}

export function ConfirmDialog({ open, onClose, onConfirm, title, message, confirmLabel = 'Confirm', danger = true, busy }) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width="max-w-md"
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className={danger ? 'btn-danger' : 'btn-primary'}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <p className="text-sm text-muted">{message}</p>
    </Modal>
  )
}

export function Drawer({ open, onClose, title, subtitle, children, footer }) {
  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="absolute right-0 top-0 h-full w-full max-w-xl bg-bg border-l border-border flex flex-col shadow-menu"
      >
        <header className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border">
          <div className="min-w-0">
            <h2 className="text-base font-semibold truncate">{title}</h2>
            {subtitle && <p className="text-xs text-muted mt-1 mono truncate">{subtitle}</p>}
          </div>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="px-5 py-3 border-t border-border">{footer}</footer>}
      </aside>
    </div>
  )
}

export function CopyButton({ value, label = 'Copy', onCopied }) {
  const handle = async () => {
    const ok = await copyText(value)
    if (ok && onCopied) onCopied()
  }
  return (
    <button className="btn-ghost btn-sm text-muted hover:text-fg" onClick={handle} type="button">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="9" y="9" width="13" height="13" rx="2" />
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
      </svg>
      {label}
    </button>
  )
}

export function Field({ label, hint, children, className = '' }) {
  return (
    <label className={`block ${className}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  )
}

export function Toggle({ checked, onChange, label, description, disabled }) {
  return (
    <label className="flex items-start gap-3 cursor-pointer select-none">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 relative inline-flex h-5 w-9 shrink-0 items-center rounded-full
          transition-colors disabled:opacity-50 ${checked ? 'bg-accent' : 'bg-border-strong'}`}
      >
        <span
          className={`pointer-events-none h-4 w-4 rounded-full bg-white shadow-sm transition-transform
            ${checked ? 'translate-x-[18px]' : 'translate-x-0.5'}`}
        />
      </button>
      <span className="min-w-0">
        <span className="block text-sm">{label}</span>
        {description && <span className="block text-xs text-muted mt-0.5">{description}</span>}
      </span>
    </label>
  )
}

export function ProviderCost({ cost, price }) {
  return (
    <span className="mono text-xs">
      {money(cost)}
      {price > cost && <span className="text-muted"> / {money(price)}</span>}
    </span>
  )
}

export function LatencyCell({ value }) {
  const slow = value > 5000
  return <span className={`mono text-xs ${slow ? 'text-warning' : ''}`}>{ms(value)}</span>
}

export function TimeCell({ value }) {
  return (
    <span className="text-xs text-muted whitespace-nowrap" title={value || ''}>
      {relative(value)}
    </span>
  )
}
