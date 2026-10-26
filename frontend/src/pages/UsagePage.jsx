import { useCallback, useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../lib/api'
import { dayLabel, money, ms, number, pct } from '../lib/format'
import { Card, ErrorBox, Spinner, StatCard } from '../components/ui'

const RANGE = [
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
]

const TOOLTIP = {
  background: 'var(--c-surface)',
  border: '1px solid var(--c-border)',
  borderRadius: 8,
  fontSize: 12,
}

/** Recharts draws nothing at all for an empty series, so show a message instead. */
function ChartPlaceholder({ height = 'h-64', message = 'No data in this range yet.' }) {
  return (
    <div className={`${height} grid place-items-center rounded-md border border-dashed border-border`}>
      <p className="text-xs text-muted">{message}</p>
    </div>
  )
}

function hasSeries(rows, key) {
  return Array.isArray(rows) && rows.some((row) => Number(row?.[key] || 0) > 0)
}

export default function UsagePage() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await api.get(`/admin/usage?days=${days}`))
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [days])

  useEffect(() => {
    load()
  }, [load])

  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!data) return <Spinner label="Loading usage" />

  const totals = data.daily.reduce(
    (acc, day) => ({
      requests: acc.requests + day.requests,
      cost: acc.cost + day.cost,
      price: acc.price + (day.price || 0),
      tokens: acc.tokens + day.tokens,
    }),
    { requests: 0, cost: 0, price: 0, tokens: 0 },
  )

  const isOwner = data.role === 'owner'
  const providerColors = [1, 2, 3, 4, 5, 6].map((n) => `var(--c-chart-${n})`)

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Usage &amp; Cost</h1>
          <p className="text-xs text-muted mt-1">
            {isOwner
              ? 'What we pay providers, and what partners are charged.'
              : 'What your traffic used, and what it cost.'}
          </p>
        </div>
        <div className="flex rounded-md border border-border p-0.5">
          {RANGE.map((option) => (
            <button
              key={option.days}
              onClick={() => setDays(option.days)}
              className={`px-2.5 h-7 rounded text-xs transition-colors ${
                days === option.days ? 'bg-accent-soft text-accent font-medium' : 'text-muted hover:text-fg'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label={`Requests (${days}d)`} value={number(totals.requests)} />
        <StatCard label="Tokens" value={number(totals.tokens)} />
        {isOwner ? (
          <>
            <StatCard label="Provider cost" value={money(totals.cost)} hint="What we pay" />
            <StatCard
              label="Partner price"
              value={money(totals.price)}
              hint={`Margin ${money(totals.price - totals.cost)}`}
            />
          </>
        ) : (
          <>
            <StatCard label="Charged" value={money(totals.cost)} hint="Your usage this period" />
            <StatCard
              label="Budget used"
              value={pct(data.budget.pct)}
              hint={`of ${money(data.budget.cap_usd)} monthly cap`}
            />
          </>
        )}
      </div>

      <Card
        title="Budget"
        subtitle={isOwner ? 'Month to date against the global cap' : 'Month to date against your cap'}
      >
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <div className="h-2 rounded-full bg-border/70 overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  data.budget.pct >= 100
                    ? 'bg-danger'
                    : data.budget.pct >= 80
                      ? 'bg-warning'
                      : 'bg-accent'
                }`}
                style={{ width: `${Math.min(100, data.budget.pct)}%` }}
              />
            </div>
            <div className="mt-2 flex justify-between text-xs text-muted">
              <span className="mono">{money(data.budget.month_to_date_usd)} spent</span>
              <span className="mono">cap {money(data.budget.cap_usd)}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="text-xl font-semibold font-mono">{pct(data.budget.pct)}</div>
            <div className="text-[10px] text-muted">of monthly cap</div>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title={isOwner ? 'Cost per day' : 'Spend per day'}
          subtitle={isOwner ? 'Provider cost vs partner price' : 'What you were charged'}
          bodyClass="p-3 pt-4"
        >
          {!hasSeries(data.daily, 'cost') ? (
            <ChartPlaceholder message="No cost recorded in this range yet." />
          ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.daily} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
                <CartesianGrid stroke="var(--c-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="day"
                  tickFormatter={dayLabel}
                  tick={{ fill: 'var(--c-muted)', fontSize: 11 }}
                  axisLine={{ stroke: 'var(--c-border)' }}
                  tickLine={false}
                  minTickGap={24}
                />
                <YAxis
                  tick={{ fill: 'var(--c-muted)', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(value) => `$${value}`}
                />
                <Tooltip
                  contentStyle={TOOLTIP}
                  labelFormatter={dayLabel}
                  formatter={(value, name) => [
                    money(value),
                    name === 'cost' ? (isOwner ? 'Cost' : 'Charged') : 'Price',
                  ]}
                />
                <Legend
                  formatter={(value) => (
                    <span style={{ color: 'var(--c-muted)', fontSize: 11 }}>
                      {value === 'cost' ? (isOwner ? 'Provider cost' : 'Charged') : 'Partner price'}
                    </span>
                  )}
                />
                <Line type="monotone" dataKey="cost" stroke="var(--c-accent)" strokeWidth={2} dot={false} />
                {isOwner && (
                  <Line
                    type="monotone"
                    dataKey="price"
                    stroke="var(--c-success)"
                    strokeWidth={2}
                    strokeDasharray="4 3"
                    dot={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
          )}
        </Card>

        <Card title="Requests by provider" subtitle={`Last ${days} days`} bodyClass="p-3 pt-4">
          {!hasSeries(data.by_provider, 'requests') ? (
            <ChartPlaceholder message="No provider traffic in this range yet." />
          ) : (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data.by_provider}
                  dataKey="requests"
                  nameKey="provider"
                  innerRadius={55}
                  outerRadius={90}
                  paddingAngle={2}
                  stroke="var(--c-bg)"
                >
                  {data.by_provider.map((entry, index) => (
                    <Cell key={entry.provider} fill={providerColors[index % providerColors.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TOOLTIP} formatter={(value) => [number(value), 'requests']} />
                <Legend
                  formatter={(value) => (
                    <span style={{ color: 'var(--c-muted)', fontSize: 11 }}>{value}</span>
                  )}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          )}
        </Card>
      </div>

      <Card title="Tokens by model" subtitle="Top models in range" bodyClass="p-3 pt-4">
        {!hasSeries(data.by_model, 'tokens') ? (
          <ChartPlaceholder message="No tokens recorded in this range yet." />
        ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.by_model.slice(0, 8)} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--c-border)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="model"
                tick={{ fill: 'var(--c-muted)', fontSize: 10 }}
                axisLine={{ stroke: 'var(--c-border)' }}
                tickLine={false}
                interval={0}
                angle={-12}
                textAnchor="end"
                height={50}
              />
              <YAxis
                tick={{ fill: 'var(--c-muted)', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(value) => (value >= 1000 ? `${value / 1000}k` : value)}
              />
              <Tooltip contentStyle={TOOLTIP} formatter={(value) => [number(value), 'tokens']} />
              <Bar
                dataKey="tokens"
                fill="var(--c-accent)"
                radius={[4, 4, 0, 0]}
                maxBarSize={64}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {isOwner ? (
          <BreakdownTable
            title="By partner"
            columns={['Partner', 'Requests', 'Cost', 'Price', 'Margin']}
            rows={data.by_partner.map((row) => [
              row.partner,
              number(row.requests),
              money(row.cost),
              money(row.price),
              money(row.margin),
            ])}
          />
        ) : (
          <BreakdownTable
            title="Your usage"
            columns={['Partner', 'Requests', 'Charged']}
            rows={data.by_partner.map((row) => [
              row.partner,
              number(row.requests),
              money(row.cost),
            ])}
          />
        )}
        <BreakdownTable
          title="By key"
          columns={isOwner ? ['Key', 'Requests', 'Cost', 'Price'] : ['Key', 'Requests', 'Charged']}
          rows={data.by_key.map((row) => [
            `${row.key} (${row.prefix}…)`,
            number(row.requests),
            money(row.cost),
            ...(isOwner ? [money(row.price)] : []),
          ])}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <BreakdownTable
          title="By model"
          columns={isOwner ? ['Model', 'Provider', 'Requests', 'Tokens', 'Cost'] : ['Model', 'Provider', 'Requests', 'Tokens', 'Charged']}
          rows={data.by_model.map((row) => [
            row.model,
            row.provider,
            number(row.requests),
            number(row.tokens),
            money(row.cost),
          ])}
        />
        <Card title="Cache" subtitle="Hit rate over the range">
          <div className="flex items-center gap-6">
            <div>
              <div className="text-3xl font-semibold font-mono">{pct(data.cache.hit_rate)}</div>
              <div className="text-xs text-muted mt-1">cache hit rate</div>
            </div>
            <dl className="flex-1 space-y-1.5 text-xs">
              <div className="flex justify-between">
                <dt className="text-muted">Requests</dt>
                <dd className="mono">{number(data.cache.requests)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Cache hits</dt>
                <dd className="mono">{number(data.cache.hits)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Saved cost</dt>
                <dd className="mono text-success">
                  {money(
                    data.cache.hits *
                      (data.daily.reduce((sum, day) => sum + day.cost, 0) /
                        Math.max(1, data.cache.requests - data.cache.hits)),
                  )}
                </dd>
              </div>
            </dl>
          </div>
          <p className="mt-4 text-xs text-muted">
            Only requests with temperature 0 are cached, and cache entries are never shared
            between partners.
          </p>
        </Card>
      </div>
    </div>
  )
}

function BreakdownTable({ title, columns, rows }) {
  return (
    <Card title={title} bodyClass="p-0">
      {rows.length === 0 ? (
        <p className="px-4 py-8 text-center text-xs text-muted">No data in this range.</p>
      ) : (
        <div className="overflow-x-auto max-h-72">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {columns.map((column, index) => (
                  <th key={column} className={`th ${index > 0 ? 'text-right' : ''}`}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="hover:bg-surface">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      className={`td text-xs ${cellIndex > 0 ? 'text-right mono' : 'truncate max-w-[180px]'}`}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
