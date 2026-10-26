import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { money, number } from '../lib/format'
import { useToast } from '../lib/toast'
import { Badge, Card, Drawer, ErrorBox, Spinner, Toggle } from '../components/ui'

export default function ModelsPage() {
  const toast = useToast()
  const [models, setModels] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [filters, setFilters] = useState({
    search: '',
    provider: '',
    freeOnly: false,
    vision: false,
    tools: false,
    enabledOnly: false,
  })

  const load = useCallback(async () => {
    try {
      const data = await api.get('/admin/models')
      setModels(data.models)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const providers = useMemo(
    () => Array.from(new Set((models || []).map((model) => model.provider))).sort(),
    [models],
  )

  const filtered = useMemo(() => {
    if (!models) return []
    const term = filters.search.toLowerCase().trim()
    return models.filter((model) => {
      if (term && !`${model.name} ${model.model_name}`.toLowerCase().includes(term)) return false
      if (filters.provider && model.provider !== filters.provider) return false
      if (filters.freeOnly && !model.is_free) return false
      if (filters.vision && !model.supports_vision) return false
      if (filters.tools && !model.supports_tools) return false
      if (filters.enabledOnly && !model.is_enabled) return false
      return true
    })
  }, [models, filters])

  const toggle = async (model, patch) => {
    try {
      const updated = await api.patch(`/admin/models/${model.id}`, patch)
      setModels((current) => current.map((item) => (item.id === updated.id ? updated : item)))
      if (selected?.id === updated.id) setSelected(updated)
      toast.success('Model updated')
    } catch (err) {
      toast.error(err.message)
    }
  }

  if (error) return <ErrorBox error={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Models</h1>
          <p className="text-xs text-muted mt-1">
            Every model the router can use. Prices are USD per 1M tokens.
          </p>
        </div>
        <span className="text-xs text-muted mono">{filtered.length} shown</span>
      </header>

      <div className="card p-3">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <input
            className="input h-8 text-xs"
            placeholder="Search models…"
            value={filters.search}
            onChange={(event) => setFilters({ ...filters, search: event.target.value })}
          />
          <select
            className="select h-8 text-xs"
            value={filters.provider}
            onChange={(event) => setFilters({ ...filters, provider: event.target.value })}
          >
            <option value="">All providers</option>
            {providers.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-3 px-1 sm:col-span-2">
            <FilterChip
              active={filters.freeOnly}
              onClick={() => setFilters({ ...filters, freeOnly: !filters.freeOnly })}
            >
              Free only
            </FilterChip>
            <FilterChip
              active={filters.vision}
              onClick={() => setFilters({ ...filters, vision: !filters.vision })}
            >
              Vision
            </FilterChip>
            <FilterChip
              active={filters.tools}
              onClick={() => setFilters({ ...filters, tools: !filters.tools })}
            >
              Tools
            </FilterChip>
            <FilterChip
              active={filters.enabledOnly}
              onClick={() => setFilters({ ...filters, enabledOnly: !filters.enabledOnly })}
            >
              Enabled
            </FilterChip>
          </div>
        </div>
      </div>

      <div className="card overflow-hidden">
        {!models ? (
          <Spinner label="Loading models" />
        ) : filtered.length === 0 ? (
          <p className="px-4 py-10 text-center text-xs text-muted">No models match those filters.</p>
        ) : (
          <div className="overflow-x-auto max-h-[70vh]">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">Model</th>
                  <th className="th">Provider</th>
                  <th className="th text-right">Context</th>
                  <th className="th text-right">Input /1M</th>
                  <th className="th text-right">Output /1M</th>
                  <th className="th">Caps</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((model) => (
                  <tr key={model.id} className="row-hover" onClick={() => setSelected(model)}>
                    <td className="td">
                      <div className="font-medium truncate max-w-[220px]">{model.name}</div>
                      {model.alias && (
                        <div className="text-xs text-muted mono truncate max-w-[220px]">
                          {model.model_name}
                        </div>
                      )}
                    </td>
                    <td className="td text-xs">{model.provider}</td>
                    <td className="td text-right mono text-xs">
                      {model.context_length ? number(model.context_length) : '—'}
                    </td>
                    <td className="td text-right mono text-xs">
                      {model.is_free ? <Badge tone="neutral">free</Badge> : money(model.input_price_per_m)}
                    </td>
                    <td className="td text-right mono text-xs">
                      {model.is_free ? '—' : money(model.output_price_per_m)}
                    </td>
                    <td className="td">
                      <div className="flex gap-1">
                        {model.supports_vision && <Badge tone="accent">vision</Badge>}
                        {model.supports_tools && <Badge tone="accent">tools</Badge>}
                        {!model.supports_vision && !model.supports_tools && (
                          <span className="text-xs text-muted">text</span>
                        )}
                      </div>
                    </td>
                    <td className="td">
                      <Badge tone={model.is_enabled ? 'success' : 'neutral'}>
                        {model.is_enabled ? 'Enabled' : 'Off'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.name}
        subtitle={selected?.model_name}
      >
        {selected && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Info label="Provider" value={selected.provider_label} />
              <Info label="Context" value={selected.context_length ? number(selected.context_length) : '—'} mono />
              <Info
                label="Input /1M"
                value={selected.is_free ? 'Free' : money(selected.input_price_per_m)}
                mono
              />
              <Info
                label="Output /1M"
                value={selected.is_free ? 'Free' : money(selected.output_price_per_m)}
                mono
              />
            </div>

            <div className="space-y-3 rounded-md border border-border p-3">
              <Toggle
                checked={selected.is_enabled}
                onChange={(value) => toggle(selected, { is_enabled: value })}
                label="Enabled"
                description="Disabled models are removed from the router and from /v1/models."
              />
            </div>

            <section>
              <h3 className="section-title mb-2">Sample request</h3>
              <pre className="rounded-md border border-border bg-surface p-3 overflow-x-auto">
                <code className="mono text-xs whitespace-pre-wrap">{`curl ${
                  window.location.origin
                }/v1/chat/completions \\
  -H "Authorization: Bearer sk-gw-..." \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${selected.name}",
    "messages": [{"role": "user", "content": "Hello"}]
  }'`}</code>
              </pre>
            </section>

            <Link to="/console/playground" className="btn-secondary w-full">
              Try in Playground
            </Link>

            {selected.prices_checked_at && (
              <p className="text-xs text-muted">
                Prices last checked {new Date(selected.prices_checked_at).toLocaleDateString()}.
                Provider prices change — verify before invoicing.
              </p>
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}

function FilterChip({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-2 h-7 rounded-full border text-xs transition-colors ${
        active
          ? 'border-accent text-accent bg-accent/10'
          : 'border-border text-muted hover:text-fg'
      }`}
      aria-pressed={active}
    >
      {children}
    </button>
  )
}

function Info({ label, value, mono }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-0.5 text-sm ${mono ? 'mono' : ''}`}>{value || '—'}</div>
    </div>
  )
}
