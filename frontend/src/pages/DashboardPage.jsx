import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../lib/api'
import { dayLabel, money, ms, number, pct, relative } from '../lib/format'
import {
  Badge,
  Card,
  EmptyState,
  ErrorBox,
  LatencyCell,
  Spinner,
  StatCard,
  StatusDot,
} from '../components/ui'

export default function DashboardPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await api.get('/admin/overview'))
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 20000)
    return () => window.clearInterval(timer)
  }, [load])

  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!data) return <Spinner label="Loading overview" />

  const { today, month, series, providers, recent, alerts } = data
  const isOwner = data.role === 'owner'

  return (
    <div className="space-y-5">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="text-xs text-muted mt-1">
            Updated {relative(data.generated_at)} · refreshes every 20s
          </p>
        </div>
        <Link to="/console/playground" className="btn-secondary">
          Open Playground
        </Link>
      </header>

      {alerts.map((alert, index) => (
        <div
          key={index}
          className={`card px-4 py-3 text-sm border-l-2 ${
            alert.level === 'danger'
              ? 'border-l-danger'
              : alert.level === 'warning'
                ? 'border-l-warning'
                : 'border-l-accent'
          }`}
          role="status"
        >
          {alert.message}
        </div>
      ))}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Requests today" value={number(today.requests)} hint={`${number(month.requests)} this month`} />
        <StatCard label="Tokens today" value={number(today.tokens)} hint={`${number(today.tokens_in)} in / ${number(today.tokens_out)} out`} />
        <StatCard
          label={isOwner ? 'Cost today' : 'Charged today'}
          value={money(today.cost_usd)}
          hint={`${money(month.cost_usd)} month-to-date`}
        />
        <StatCard
          label="Error rate"
          value={pct(today.error_rate)}
          tone={today.error_rate > 5 ? 'danger' : today.error_rate > 1 ? 'warning' : 'neutral'}
          hint={`${number(today.errors)} failed of ${number(today.requests)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card
          className="lg:col-span-2 min-w-0"
          title="Requests, last 7 days"
          subtitle="Daily request volume"
          bodyClass="p-3 pt-4"
        >
          {series.every((point) => point.requests === 0) ? (
            <div className="h-56 grid place-items-center rounded-md border border-dashed border-border">
              <p className="text-xs text-muted">No requests in the last 7 days yet.</p>
            </div>
          ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
                <defs>
                  <linearGradient id="requests" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--c-accent)" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="var(--c-accent)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--c-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="day"
                  tickFormatter={dayLabel}
                  tick={{ fill: 'var(--c-muted)', fontSize: 11 }}
                  axisLine={{ stroke: 'var(--c-border)' }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: 'var(--c-muted)', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--c-surface)',
                    border: '1px solid var(--c-border)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelFormatter={dayLabel}
                  formatter={(value) => [number(value), 'requests']}
                />
                <Area
                  type="monotone"
                  dataKey="requests"
                  stroke="var(--c-accent)"
                  strokeWidth={2}
                  fill="url(#requests)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          )}
        </Card>

        <Card title="Budget" subtitle={isOwner ? 'Global monthly cap' : 'Your monthly cap'}>
          <div className="space-y-3">
            <div>
              <div className="flex items-baseline justify-between">
                <span className="text-2xl font-semibold font-mono">{money(month.cost_usd)}</span>
                <span className="text-xs text-muted">of {money(month.cap_usd)}</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-border/70 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    month.spend_pct >= 100
                      ? 'bg-danger'
                      : month.spend_pct >= 80
                        ? 'bg-warning'
                        : 'bg-accent'
                  }`}
                  style={{ width: `${Math.min(100, month.spend_pct)}%` }}
                />
              </div>
              <p className="mt-2 text-xs text-muted">
                {pct(month.spend_pct)} used
                {month.spend_pct >= 100 && ' — paid routes are blocked'}
              </p>
            </div>

            <dl className="pt-3 border-t border-border space-y-2 text-xs">
              {isOwner ? (
                <>
                  <div className="flex justify-between">
                    <dt className="text-muted">Charged to partners</dt>
                    <dd className="mono">{money(month.price_usd)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-muted">Margin</dt>
                    <dd className={`mono ${month.margin_usd >= 0 ? 'text-success' : 'text-danger'}`}>
                      {money(month.margin_usd)}
                    </dd>
                  </div>
                </>
              ) : (
                <div className="flex justify-between">
                  <dt className="text-muted">Requests this month</dt>
                  <dd className="mono">{number(month.requests)}</dd>
                </div>
              )}
              <div className="flex justify-between">
                <dt className="text-muted">Cached entries</dt>
                <dd className="mono">{number(data.cache.entries)}</dd>
              </div>
            </dl>
          </div>
        </Card>
      </div>

      <div className={`grid gap-4 min-w-0 ${isOwner ? 'lg:grid-cols-2' : ''}`}>
        {isOwner && (
        <Card title="Provider health" subtitle="Circuit breaker state" bodyClass="p-0">
          {providers.length === 0 ? (
            <EmptyState
              title="No providers yet"
              description="Add a provider key to start routing requests."
              action={
                <Link to="/console/providers" className="btn-primary btn-sm">
                  Add provider
                </Link>
              }
            />
          ) : (
            <ul className="divide-y divide-border">
              {providers.map((provider) => (
                <li key={provider.id} className="flex items-center gap-2 px-3 py-2.5 min-w-0">
                  <StatusDot status={provider.status} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm truncate">{provider.label}</div>
                    {provider.last_error && (
                      <div className="text-xs text-muted truncate" title={provider.last_error}>
                        {provider.last_error}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {provider.free_tier && <Badge tone="neutral">free</Badge>}
                    {!provider.configured && <Badge tone="warning">no key</Badge>}
                    <span className="mono text-xs text-muted w-14 text-right">
                      {provider.last_latency_ms ? ms(provider.last_latency_ms) : '—'}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        )}

        <Card title="Recent activity" subtitle="Last 10 requests" bodyClass="p-0" className="min-w-0">
          {recent.length === 0 ? (
            <EmptyState
              title="No requests yet"
              description="Once your sites call the gateway, requests appear here."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th">Time</th>
                    <th className="th">Model</th>
                    <th className="th">Provider</th>
                    <th className="th text-right">Latency</th>
                    <th className="th text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((row) => (
                    <tr key={row.id} className="hover:bg-surface">
                      <td className="td whitespace-nowrap text-xs text-muted">{relative(row.time)}</td>
                      <td className="td mono text-xs truncate max-w-[140px]">{row.requested_model}</td>
                      <td className="td text-xs">{row.provider || '—'}</td>
                      <td className="td text-right">
                        <LatencyCell value={row.latency_ms} />
                      </td>
                      <td className="td text-right">
                        <Badge tone={row.status_code === 200 ? 'success' : 'danger'}>
                          {row.status_code}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
