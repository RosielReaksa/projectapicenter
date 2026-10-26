/** Formatting helpers. Numbers and keys always render in mono. */

export function money(value, digits = 4) {
  const number = Number(value || 0)
  if (number === 0) return '$0'
  if (number < 0.0001) return `$${number.toExponential(1)}`
  return `$${number.toFixed(number < 1 ? digits : 2)}`
}

export function compact(value) {
  const number = Number(value || 0)
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}k`
  return String(number)
}

export function number(value) {
  return Number(value || 0).toLocaleString()
}

export function ms(value) {
  const v = Number(value || 0)
  if (v >= 1000) return `${(v / 1000).toFixed(1)}s`
  return `${v}ms`
}

export function relative(iso) {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`
  return new Date(iso).toLocaleDateString()
}

export function datetime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function dayLabel(day) {
  const date = new Date(`${day}T00:00:00Z`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function pct(value) {
  return `${Number(value || 0).toFixed(1)}%`
}

export function statusTone(code) {
  if (code === 200) return 'success'
  if (code === 429) return 'warning'
  if (code >= 500) return 'danger'
  return 'danger'
}

export function healthTone(status) {
  switch (status) {
    case 'healthy':
      return 'success'
    case 'rate_limited':
    case 'cooling_down':
    case 'degraded':
      return 'warning'
    case 'down':
    case 'misconfigured':
      return 'danger'
    default:
      return 'neutral'
  }
}

export function healthLabel(status) {
  switch (status) {
    case 'healthy':
      return 'Healthy'
    case 'rate_limited':
      return 'Rate limited'
    case 'cooling_down':
      return 'Cooling down'
    case 'degraded':
      return 'Degraded'
    case 'down':
      return 'Down'
    case 'misconfigured':
      return 'Bad key'
    default:
      return 'Unknown'
  }
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API needs a secure context; fall back to a hidden textarea.
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  }
}
