import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { money, ms, number } from '../lib/format'
import { useToast } from '../lib/toast'
import { Badge, Card, CopyButton, Field, Spinner } from '../components/ui'

const DEFAULT_PARAMS = { temperature: 0.7, max_tokens: 1024, top_p: 1, stop: '' }

export default function PlaygroundPage() {
  const toast = useToast()
  const [models, setModels] = useState([])
  const [presets, setPresets] = useState([])
  const [model, setModel] = useState('auto')
  const [system, setSystem] = useState('')
  const [messages, setMessages] = useState([{ role: 'user', content: '' }])
  const [params, setParams] = useState(DEFAULT_PARAMS)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [compare, setCompare] = useState([])
  const [showCode, setShowCode] = useState(false)
  const bottom = useRef(null)

  useEffect(() => {
    api
      .get('/admin/lookup')
      .then((data) => setModels(data.models.filter((item) => item.is_enabled)))
      .catch(() => {})
    api
      .get('/admin/presets')
      .then((data) => setPresets(data.presets))
      .catch(() => {})
  }, [])

  const body = useMemo(() => {
    const payload = {
      model,
      system: system.trim() || undefined,
      messages: messages.filter((message) => message.content.trim() || message.role === 'assistant'),
      temperature: Number(params.temperature),
      max_tokens: Number(params.max_tokens),
      top_p: Number(params.top_p),
    }
    if (params.stop.trim()) payload.stop = params.stop.split(',').map((item) => item.trim())
    return payload
  }, [model, system, messages, params])

  const run = async (targetModel = model) => {
    if (!body.messages.some((message) => message.content.trim())) {
      toast.error('Type a message first')
      return
    }
    setRunning(true)
    setError(null)
    try {
      const response = await api.post('/admin/playground/run', { ...body, model: targetModel })
      setResult(response)
      setMessages((current) => [
        ...current,
        { role: 'assistant', content: response.content },
        { role: 'user', content: '' },
      ])
      setTimeout(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    } catch (err) {
      setError(err)
    } finally {
      setRunning(false)
    }
  }

  const runCompare = async () => {
    const picks = compare.filter(Boolean)
    if (picks.length < 2) {
      toast.error('Pick at least two models to compare')
      return
    }
    setRunning(true)
    setError(null)
    try {
      const results = await Promise.all(
        picks.map(async (target) => {
          try {
            const response = await api.post('/admin/playground/run', { ...body, model: target })
            return { model: target, response }
          } catch (err) {
            return { model: target, error: err.message }
          }
        }),
      )
      setResult({ compare: results })
    } finally {
      setRunning(false)
    }
  }

  const savePreset = async () => {
    const name = window.prompt('Preset name')
    if (!name) return
    try {
      await api.post('/admin/presets', {
        name,
        system_prompt: system || null,
        model_id: null,
        params: { model, ...params },
      })
      toast.success('Preset saved')
      const data = await api.get('/admin/presets')
      setPresets(data.presets)
    } catch (err) {
      toast.error(err.message)
    }
  }

  const clear = () => {
    setMessages([{ role: 'user', content: '' }])
    setResult(null)
    setError(null)
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      {/* Thread */}
      <div className="space-y-3 min-w-0">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="page-title">Playground</h1>
            <p className="text-xs text-muted mt-1">
              Runs through the same pipeline, limits and logging as real API calls.
            </p>
          </div>
          <div className="flex gap-2">
            <button className="btn-ghost btn-sm" onClick={() => setShowCode(true)}>
              View code
            </button>
            <button className="btn-ghost btn-sm" onClick={savePreset}>
              Save preset
            </button>
            <button className="btn-ghost btn-sm" onClick={clear}>
              Clear
            </button>
          </div>
        </header>

        <Card bodyClass="p-0">
          <div className="border-b border-border p-3">
            <Field label="System prompt">
              <textarea
                className="textarea text-xs"
                rows={2}
                placeholder="You are a helpful assistant…"
                value={system}
                onChange={(event) => setSystem(event.target.value)}
              />
            </Field>
          </div>

          <div className="max-h-[45vh] overflow-y-auto p-3 space-y-2">
            {messages.map((message, index) => (
              <div
                key={index}
                className={`rounded-md border p-2.5 ${
                  message.role === 'user'
                    ? 'border-border bg-surface'
                    : 'border-accent/30 bg-accent/5'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] uppercase tracking-wide text-muted">
                    {message.role}
                  </span>
                  {message.role === 'assistant' && (
                    <CopyButton value={message.content} label="" />
                  )}
                </div>
                {message.role === 'user' ? (
                  <textarea
                    className="w-full bg-transparent border-0 outline-none resize-none text-sm"
                    rows={Math.min(8, Math.max(2, message.content.split('\n').length))}
                    placeholder="Type your message…"
                    value={message.content}
                    onChange={(event) => {
                      const next = [...messages]
                      next[index] = { ...message, content: event.target.value }
                      setMessages(next)
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) run()
                    }}
                  />
                ) : (
                  <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                )}
              </div>
            ))}
            <div ref={bottom} />
          </div>

          <div className="border-t border-border p-3 flex items-center gap-2">
            <button className="btn-primary" onClick={() => run()} disabled={running}>
              {running ? 'Running…' : 'Run'}
            </button>
            <span className="text-xs text-muted">⌘/Ctrl + Enter</span>
            {result?.gateway && (
              <span className="ml-auto flex flex-wrap items-center gap-2 text-xs text-muted">
                <Badge tone="accent">{result.gateway.provider}</Badge>
                <span className="mono">{result.gateway.model}</span>
                <span className="mono">{ms(result.gateway.latency_ms)}</span>
                <span className="mono">
                  {number(result.gateway.prompt_tokens)} in / {number(result.gateway.completion_tokens)} out
                </span>
                <span className="mono">{money(result.gateway.cost_usd)}</span>
                {result.gateway.cached && <Badge tone="accent">cached</Badge>}
              </span>
            )}
          </div>
        </Card>

        {error && (
          <Card className="border-danger/40">
            <p className="text-sm text-danger font-medium">{error.code || 'Error'}</p>
            <p className="text-xs text-muted mt-1">{error.message}</p>
            {error.requestId && <p className="text-xs text-muted mono mt-1">{error.requestId}</p>}
            {error.attempts?.length > 0 && (
              <ul className="mt-2 space-y-1">
                {error.attempts.map((attempt, index) => (
                  <li key={index} className="text-xs mono text-muted">
                    {attempt.provider} / {attempt.model}: {attempt.status} {attempt.error}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {result?.compare && (
          <div className="grid gap-3 md:grid-cols-2">
            {result.compare.map((entry) => (
              <Card key={entry.model} title={entry.model} bodyClass="p-3">
                {entry.error ? (
                  <p className="text-xs text-danger">{entry.error}</p>
                ) : (
                  <>
                    <p className="text-sm whitespace-pre-wrap">{entry.response.content}</p>
                    <div className="mt-3 pt-3 border-t border-border flex flex-wrap gap-2 text-[11px] text-muted">
                      <span className="mono">{entry.response.gateway.provider}</span>
                      <span className="mono">{ms(entry.response.gateway.latency_ms)}</span>
                      <span className="mono">{money(entry.response.gateway.cost_usd)}</span>
                    </div>
                  </>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Parameters */}
      <aside className="space-y-3">
        <Card title="Model" bodyClass="p-3 space-y-3">
          <select className="select" value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="auto">auto — let the router decide</option>
            {models.map((item) => (
              <option key={item.id} value={item.name}>
                {item.name} — {item.provider}
                {item.is_free ? ' (free)' : ''}
              </option>
            ))}
          </select>
          <Field label="Preset">
            <select
              className="select"
              value=""
              onChange={(event) => {
                const preset = presets.find((item) => String(item.id) === event.target.value)
                if (!preset) return
                setSystem(preset.system_prompt || '')
                setParams({ ...DEFAULT_PARAMS, ...(preset.params || {}) })
                if (preset.params?.model) setModel(preset.params.model)
                event.target.value = ''
              }}
            >
              <option value="">Load a preset…</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
          </Field>
        </Card>

        <Card title="Parameters" bodyClass="p-3 space-y-3">
          <Field label={`Temperature — ${params.temperature}`}>
            <input
              type="range"
              min="0"
              max="2"
              step="0.1"
              className="w-full accent-[var(--c-accent)]"
              value={params.temperature}
              onChange={(event) => setParams({ ...params, temperature: event.target.value })}
            />
          </Field>
          <Field label="Max tokens">
            <input
              className="input mono h-8 text-xs"
              type="number"
              min="1"
              value={params.max_tokens}
              onChange={(event) => setParams({ ...params, max_tokens: event.target.value })}
            />
          </Field>
          <Field label="Top P">
            <input
              className="input mono h-8 text-xs"
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={params.top_p}
              onChange={(event) => setParams({ ...params, top_p: event.target.value })}
            />
          </Field>
          <Field label="Stop sequences" hint="Comma separated">
            <input
              className="input mono h-8 text-xs"
              value={params.stop}
              onChange={(event) => setParams({ ...params, stop: event.target.value })}
            />
          </Field>
          <p className="text-[11px] text-muted">
            Temperature 0 makes the reply cacheable. Cached replies are free and marked as such.
          </p>
        </Card>

        <Card title="Compare mode" bodyClass="p-3 space-y-2">
          {[0, 1].map((index) => (
            <select
              key={index}
              className="select h-8 text-xs"
              value={compare[index] || ''}
              onChange={(event) => {
                const next = [...compare]
                next[index] = event.target.value
                setCompare(next)
              }}
            >
              <option value="">Model {index + 1}…</option>
              {models.map((item) => (
                <option key={item.id} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          ))}
          <button className="btn-secondary w-full" onClick={runCompare} disabled={running}>
            Run side by side
          </button>
        </Card>
      </aside>

      {showCode && (
        <CodeDialog
          model={model}
          body={body}
          onClose={() => setShowCode(false)}
          onCopied={() => toast.success('Copied')}
        />
      )}
    </div>
  )
}

function CodeDialog({ model, body, onClose, onCopied }) {
  const [tab, setTab] = useState('curl')
  const payload = {
    model,
    messages: body.messages.map((message) => ({ role: message.role, content: message.content })),
    temperature: body.temperature,
    max_tokens: body.max_tokens,
  }
  if (body.system) payload.messages = [{ role: 'system', content: body.system }, ...payload.messages]

  const origin = window.location.origin
  const json = JSON.stringify(payload, null, 2)

  const snippets = {
    curl: `curl ${origin}/v1/chat/completions \\
  -H "Authorization: Bearer sk-gw-YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '${JSON.stringify(payload)}'`,
    python: `import httpx

response = httpx.post(
    "${origin}/v1/chat/completions",
    headers={"Authorization": "Bearer sk-gw-YOUR_KEY"},
    json=${json.replace(/\n/g, '\n    ')},
    timeout=60,
)
data = response.json()
print(data["choices"][0]["message"]["content"])
print(data["gateway"])  # provider, cost, cached, fallbacks`,
    javascript: `const response = await fetch("${origin}/v1/chat/completions", {
  method: "POST",
  headers: {
    Authorization: "Bearer sk-gw-YOUR_KEY",
    "Content-Type": "application/json",
  },
  body: JSON.stringify(${json.replace(/\n/g, '\n  ')}),
})

const data = await response.json()
console.log(data.choices[0].message.content)`,
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
      <div className="relative w-full max-w-2xl card bg-bg">
        <header className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex gap-1">
            {Object.keys(snippets).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`px-2.5 h-7 rounded text-xs ${
                  tab === key ? 'bg-accent-soft text-accent font-medium' : 'text-muted hover:text-fg'
                }`}
              >
                {key}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <CopyButton value={snippets[tab]} label="Copy" onCopied={onCopied} />
            <button className="btn-ghost btn-sm" onClick={onClose}>
              Close
            </button>
          </div>
        </header>
        <pre className="p-4 overflow-x-auto max-h-[60vh]">
          <code className="mono text-xs whitespace-pre-wrap">{snippets[tab]}</code>
        </pre>
      </div>
    </div>
  )
}
