import { useEffect, useMemo, useState } from 'react'
import { api } from '../../lib/api'
import { number } from '../../lib/format'
import { useSite } from '../../lib/site'
import { Section } from '../../components/PublicLayout'
import { Badge, ErrorBox, Spinner } from '../../components/ui'

export default function PublicModelsPage() {
  const site = useSite()
  const [models, setModels] = useState(null)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [vision, setVision] = useState(false)
  const [tools, setTools] = useState(false)
  const [longContext, setLongContext] = useState(false)

  const load = () => {
    setError(null)
    api
      .get('/public/models')
      .then((data) => setModels(data.models))
      .catch(setError)
  }

  useEffect(load, [])

  const filtered = useMemo(() => {
    if (!models) return []
    const term = search.trim().toLowerCase()
    return models.filter((model) => {
      if (term && !model.name.toLowerCase().includes(term)) return false
      if (vision && !model.supports_vision) return false
      if (tools && !model.supports_tools) return false
      if (longContext && (model.context_length || 0) < 128000) return false
      return true
    })
  }, [models, search, vision, tools, longContext])

  return (
    <Section className="py-12 sm:py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Models</h1>
      <p className="mt-2 text-sm text-muted">
        Every model available through the gateway. Access is by invitation.
      </p>

      <div className="mt-6 card p-3">
        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
          <input
            className="input h-8 text-xs"
            placeholder="Search models…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search models"
          />
          <div className="flex items-center gap-2 flex-wrap">
            <Chip active={vision} onClick={() => setVision(!vision)}>Vision</Chip>
            <Chip active={tools} onClick={() => setTools(!tools)}>Tools</Chip>
            <Chip active={longContext} onClick={() => setLongContext(!longContext)}>
              128k+ context
            </Chip>
          </div>
        </div>
      </div>

      {error ? (
        <div className="mt-6">
          <ErrorBox error={error} onRetry={load} />
        </div>
      ) : !models ? (
        <Spinner label="Loading models" />
      ) : (
        <>
          <p className="mt-4 text-xs text-muted mono">{filtered.length} models</p>
          <div className="mt-2 card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th">Model</th>
                    <th className="th text-right">Context</th>
                    <th className="th">Capabilities</th>
                    {site.public_pricing && <th className="th text-right">Input /1M</th>}
                    {site.public_pricing && <th className="th text-right">Output /1M</th>}
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr>
                      <td className="td text-xs text-muted" colSpan={5}>
                        No models match those filters.
                      </td>
                    </tr>
                  ) : (
                    filtered.map((model) => (
                      <tr key={model.id}>
                        <td className="td mono text-xs">{model.name}</td>
                        <td className="td text-right mono text-xs">
                          {model.context_length ? number(model.context_length) : '—'}
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
                        {site.public_pricing && (
                          <td className="td text-right mono text-xs">
                            {model.free ? 'Free' : `$${model.input_price_per_m}`}
                          </td>
                        )}
                        {site.public_pricing && (
                          <td className="td text-right mono text-xs">
                            {model.free ? 'Free' : `$${model.output_price_per_m}`}
                          </td>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </Section>
  )
}

function Chip({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`px-2.5 h-7 rounded-full border text-xs transition-colors ${
        active ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:text-fg'
      }`}
    >
      {children}
    </button>
  )
}
