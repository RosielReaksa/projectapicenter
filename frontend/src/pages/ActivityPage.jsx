import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, qs } from '../lib/api'
import { datetime, money, ms, number, relative, statusTone } from '../lib/format'
import { useToast } from '../lib/toast'
import {
  Badge,
  CopyButton,
  Drawer,
  EmptyState,
  ErrorBox,
  LatencyCell,
  Spinner,
} from '../components/ui'

const PAGE = 50

export default function ActivityPage() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [detail, setDetail] = useState(null)
  const [filters, setFilters] = useState({
    provider: '',
    status: '',
    cached: '',
    key_id: '',
    partner_id: '',
    search: params.get('search') || '',
    start: '',
    end: '',
  })
  const [offset, setOffset] = useState(0)
  const [lookup, setLookup] = useState({ providers: [], keys: [], partners: [] })

  useEffect(() => {
    api.get('/admin/lookup').then(setLookup).catch(() => {})
  }, [])

  const load = useCallback(async () => {
    try {
      const query = qs({ ...filters, limit: PAGE, offset })
      const result = await api.get(`/admin/activity${query}`)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [filters, offset])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const timer = window.setInterval(load, 15000)
    return () => window.clearInterval(timer)
  }, [load])

  const update = (key, value) => {
    setOffset(0)
    setFilters((current) => ({ ...current, [key]: value }))
  }

  const openDetail = async (row) => {
    try {
      setDetail(await api.get(`/admin/activity/${row.id}`))
    } catch (err) {
      toast.error(err.message)
    }
  }

  const exportCsv = async () => {
    try {
      const query = qs(filters)
      const text = await api.text(`/admin/activity/export/csv${query}`)
      const blob = new Blob([text], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'activity.csv'
      link.click()
      URL.revokeObjectURL(url)
      toast.success('CSV exported')
    } catch (err) {
      toast.error(err.message)
    }
  }

  const activeFilters = useMemo(
    () => Object.values(filters).filter(Boolean).length,
    [filters],
  )

  if (error) return <ErrorBox error={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Activity</h1>
          <p className="text-xs text-muted mt-1">
            Every request through the gateway. Click a row for details.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {activeFilters > 0 && (
            <button
              className="btn-ghost btn-sm"
              onClick={() => {
                setFilters({
                  provider: '',
                  status: '',
                  cached: '',
                  key_id: '',
                  partner_id: '',
                  search: '',
                  start: '',
                  end: '',
                })
                setOffset(0)
                setParams({})
              }}
            >
              Clear filters ({activeFilters})
            </button>
          )}
          <button className="btn-secondary btn-sm" onClick={exportCsv}>
            Export CSV
          </button>
        </div>
      </header>

      <div className="card p-3">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <input
            className="input h-8 text-xs"
            placeholder="Request id…"
            value={filters.search}
            onChange={(event) => update('search', event.target.value)}
          />
          <select
            className="select h-8 text-xs"
            value={filters.provider}
            onChange={(event) => update('provider', event.target.value)}
          >
            <option value="">All providers</option>
            {lookup.providers.map((provider) => (
              <option key={provider.id} value={provider.name}>
                {provider.label}
              </option>
            ))}
          </select>
          <select
            className="select h-8 text-xs"
            value={filters.status}
            onChange={(event) => update('status', event.target.value)}
          >
            <option value="">Any status</option>
            <option value="success">Success</option>
            <option value="error">Errors only</option>
          </select>
          <select
            className="select h-8 text-xs"
            value={filters.cached}
            onChange={(event) => update('cached', event.target.value)}
          >
            <option value="">Cached or not</option>
            <option value="true">Cached only</option>
            <option value="false">Not cached</option>
          </select>
          <select
            className="select h-8 text-xs"
            value={filters.key_id}
            onChange={(event) => update('key_id', event.target.value)}
          >
            <option value="">All keys</option>
            {lookup.keys.map((key) => (
              <option key={key.id} value={key.id}>
                {key.name} ({key.prefix}…)
              </option>
            ))}
          </select>
          <select
            className="select h-8 text-xs"
            value={filters.partner_id}
            onChange={(event) => update('partner_id', event.target.value)}
          >
            <option value="">All partners</option>
            {lookup.partners.map((partner) => (
              <option key={partner.id} value={partner.id}>
                {partner.name}
              </option>
            ))}
          </select>
          <input
            className="input h-8 text-xs"
            type="date"
            value={filters.start}
            onChange={(event) => update('start', event.target.value)}
            aria-label="From date"
          />
          <input
            className="input h-8 text-xs"
            type="date"
            value={filters.end}
            onChange={(event) => update('end', event.target.value)}
            aria-label="To date"
          />
        </div>
      </div>

      <div className="card overflow-hidden">
        {!data ? (
          <Spinner label="Loading activity" />
        ) : data.requests.length === 0 ? (
          <EmptyState
            title="No requests match"
            description="Adjust the filters, or wait for traffic to arrive."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">Time</th>
                  <th className="th">Partner</th>
                  <th className="th">Model requested</th>
                  <th className="th">Provider</th>
                  <th className="th text-right">Status</th>
                  <th className="th text-right">Latency</th>
                  <th className="th text-right">Tokens</th>
                  <th className="th text-right">Cost</th>
                  <th className="th">Flags</th>
                </tr>
              </thead>
              <tbody>
                {data.requests.map((row) => (
                  <tr key={row.id} className="row-hover" onClick={() => openDetail(row)}>
                    <td className="td whitespace-nowrap text-xs text-muted" title={row.time}>
                      {relative(row.time)}
                    </td>
                    <td className="td text-xs">{row.partner || '—'}</td>
                    <td className="td mono text-xs max-w-[160px] truncate">{row.requested_model}</td>
                    <td className="td text-xs">
                      {row.provider || <span className="text-muted">—</span>}
                      {row.model && row.model !== row.requested_model && (
                        <span className="text-muted mono"> → {row.model}</span>
                      )}
                    </td>
                    <td className="td text-right">
                      <Badge tone={statusTone(row.status_code)}>{row.status_code}</Badge>
                    </td>
                    <td className="td text-right">
                      <LatencyCell value={row.latency_ms} />
                    </td>
                    <td className="td text-right mono text-xs">{number(row.total_tokens)}</td>
                    <td className="td text-right mono text-xs">{money(row.cost_usd)}</td>
                    <td className="td">
                      <div className="flex gap-1">
                        {row.cached && <Badge tone="accent">cached</Badge>}
                        {row.fallbacks_tried?.length > 0 && (
                          <Badge tone="warning">{row.fallbacks_tried.length} retry</Badge>
                        )}
                        {row.source === 'playground' && <Badge tone="neutral">playground</Badge>}
                        {row.end_user && (
                          <Badge tone="neutral" className="max-w-[100px] truncate">
                            {row.end_user}
                          </Badge>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.total > PAGE && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border text-xs text-muted">
            <span>
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {number(data.total)}
            </span>
            <div className="flex gap-2">
              <button
                className="btn-secondary btn-sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                Previous
              </button>
              <button
                className="btn-secondary btn-sm"
                disabled={offset + PAGE >= data.total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      <RequestDrawer detail={detail} onClose={() => setDetail(null)} />
    </div>
  )
}

function Row({ label, children, mono = false }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 border-b border-border last:border-0">
      <span className="text-xs text-muted shrink-0">{label}</span>
      <span className={`text-xs text-right break-all ${mono ? 'mono' : ''}`}>{children}</span>
    </div>
  )
}

function RequestDrawer({ detail, onClose }) {
  if (!detail) return null
  const payload = detail.payload

  return (
    <Drawer
      open={!!detail}
      onClose={onClose}
      title={`Request ${detail.request_id}`}
      subtitle={datetime(detail.time)}
      footer={
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted">
            {detail.source === 'playground' ? 'Sent from the Playground' : 'Sent by a partner'}
          </span>
          <CopyButton value={detail.request_id} label="Copy id" />
        </div>
      }
    >
      <div className="space-y-4">
        <section>
          <h3 className="section-title mb-2">Routing</h3>
          <Row label="Partner">{detail.partner || '—'}</Row>
          <Row label="Key" mono>
            {detail.key_name} {detail.key_prefix && `(${detail.key_prefix}…)`}
          </Row>
          <Row label="Requested model" mono>
            {detail.requested_model}
          </Row>
          <Row label="Answered by">
            {detail.provider ? (
              <span>
                {detail.provider} <span className="mono text-muted">/ {detail.model}</span>
              </span>
            ) : (
              '—'
            )}
          </Row>
          <Row label="Status">
            <Badge tone={statusTone(detail.status_code)}>{detail.status_code}</Badge>
          </Row>
          <Row label="Latency" mono>
            {ms(detail.latency_ms)}
          </Row>
          {detail.end_user && <Row label="End user" mono>{detail.end_user}</Row>}
        </section>

        <section>
          <h3 className="section-title mb-2">Usage & cost</h3>
          <Row label="Prompt tokens" mono>
            {number(detail.prompt_tokens)}
          </Row>
          <Row label="Completion tokens" mono>
            {number(detail.completion_tokens)}
          </Row>
          <Row label="We pay" mono>
            {money(detail.cost_usd)}
          </Row>
          <Row label="Partner pays" mono>
            {money(detail.price_usd)}
          </Row>
          <Row label="Cached">{detail.cached ? 'Yes (free)' : 'No'}</Row>
        </section>

        {detail.fallbacks_tried?.length > 0 && (
          <section>
            <h3 className="section-title mb-2">Fallbacks tried</h3>
            <div className="space-y-1">
              {detail.fallbacks_tried.map((attempt, index) => (
                <div
                  key={index}
                  className="flex items-start gap-2 rounded-md border border-border bg-surface px-2.5 py-2 text-xs"
                >
                  <Badge tone="warning">{attempt.status || 'ERR'}</Badge>
                  <div className="min-w-0">
                    <div className="mono truncate">
                      {attempt.provider} / {attempt.model}
                    </div>
                    <div className="text-muted line-clamp-2">{attempt.error}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {detail.error && (
          <section>
            <h3 className="section-title mb-2 text-danger">Error</h3>
            <pre className="rounded-md border border-danger/30 bg-danger/5 p-3 overflow-x-auto">
              <code className="mono text-xs whitespace-pre-wrap">{detail.error}</code>
            </pre>
          </section>
        )}

        <section>
          <h3 className="section-title mb-2">Stored payload</h3>
          {payload ? (
            <pre className="rounded-md border border-border bg-surface p-3 overflow-x-auto max-h-80">
              <code className="mono text-xs whitespace-pre-wrap">
                {JSON.stringify(payload, null, 2)}
              </code>
            </pre>
          ) : (
            <p className="text-xs text-muted">
              Nothing stored. Prompt logging is set to metadata-only (or off), so request and
              response text is never written to the database.
            </p>
          )}
        </section>
      </div>
    </Drawer>
  )
}
