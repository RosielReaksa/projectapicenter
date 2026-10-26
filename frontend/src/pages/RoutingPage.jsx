import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { money } from '../lib/format'
import { useToast } from '../lib/toast'
import { Badge, Card, ErrorBox, Field, Modal, Spinner, Toggle } from '../components/ui'

const CONDITION_HELP = {
  has_image: 'true / false',
  needs_tools: 'true / false',
  max_chars: 'e.g. 2000',
  min_chars: 'e.g. 2000',
  preset: 'preset name',
}

const ACTION_HELP = {
  use_models: 'list of model ids',
  prefer_provider: 'provider name',
  exclude_providers: 'list of provider names',
}

export default function RoutingPage() {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [ruleModal, setRuleModal] = useState(null)
  const [preview, setPreview] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.get('/admin/routing'))
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const saveSettings = async (patch) => {
    setSaving(true)
    try {
      await api.put('/admin/routing', patch)
      toast.success('Routing updated')
      await load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const move = async (index, direction) => {
    const list = [...data.auto_route]
    const target = index + direction
    if (target < 0 || target >= list.length) return
    ;[list[index], list[target]] = [list[target], list[index]]
    setData({ ...data, auto_route: list })
    await saveSettings({ auto_route: list })
  }

  const addToRoute = async (modelId) => {
    if (data.auto_route.includes(modelId)) return
    const list = [...data.auto_route, modelId]
    setData({ ...data, auto_route: list })
    await saveSettings({ auto_route: list })
  }

  const removeFromRoute = async (modelId) => {
    const list = data.auto_route.filter((id) => id !== modelId)
    setData({ ...data, auto_route: list })
    await saveSettings({ auto_route: list })
  }

  const saveRule = async (rule) => {
    try {
      if (rule.id) await api.patch(`/admin/routing/rules/${rule.id}`, rule)
      else await api.post('/admin/routing/rules', rule)
      toast.success('Rule saved')
      setRuleModal(null)
      load()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const deleteRule = async (rule) => {
    try {
      await api.del(`/admin/routing/rules/${rule.id}`)
      toast.success('Rule deleted')
      load()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const runPreview = async () => {
    setPreviewBusy(true)
    try {
      setPreview(
        await api.post('/admin/routing/preview', {
          model: 'auto',
          messages: [{ role: 'user', content: 'hello there' }],
        }),
      )
    } catch (err) {
      toast.error(err.message)
    } finally {
      setPreviewBusy(false)
    }
  }

  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!data) return <Spinner label="Loading routing" />

  const byId = new Map(data.catalog.map((model) => [model.id, model]))
  const routed = data.auto_route.map((id) => byId.get(id)).filter(Boolean)

  return (
    <div className="space-y-5">
      <header>
        <h1 className="page-title">Routing</h1>
        <p className="text-xs text-muted mt-1">
          How <code className="mono">model: "auto"</code> picks a provider, and what happens when
          one fails.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Auto route order"
          subtitle="Drag-free reordering: lower entries are tried first"
          bodyClass="p-0"
        >
          {routed.length === 0 ? (
            <div className="px-4 py-6 text-center">
              <p className="text-sm">No custom order</p>
              <p className="text-xs text-muted mt-1">
                Without an order, the router tries the cheapest healthy paid model first and free
                tiers last.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {routed.map((model, index) => (
                <li key={model.id} className="flex items-center gap-2 px-3 py-2">
                  <span className="mono text-xs text-muted w-5">{index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm truncate">{model.name}</div>
                    <div className="text-xs text-muted">{model.provider_label}</div>
                  </div>
                  {model.free_tier_provider && <Badge tone="neutral">free</Badge>}
                  <div className="flex gap-0.5">
                    <button
                      className="btn-ghost btn-sm px-1.5"
                      onClick={() => move(index, -1)}
                      disabled={index === 0}
                      aria-label="Move up"
                    >
                      ↑
                    </button>
                    <button
                      className="btn-ghost btn-sm px-1.5"
                      onClick={() => move(index, 1)}
                      disabled={index === routed.length - 1}
                      aria-label="Move down"
                    >
                      ↓
                    </button>
                    <button
                      className="btn-ghost btn-sm px-1.5 text-danger"
                      onClick={() => removeFromRoute(model.id)}
                      aria-label="Remove"
                    >
                      ×
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="p-3 border-t border-border">
            <select
              className="select h-8 text-xs"
              value=""
              onChange={(event) => {
                if (event.target.value) addToRoute(Number(event.target.value))
                event.target.value = ''
              }}
            >
              <option value="">Add a model to the order…</option>
              {data.catalog
                .filter((model) => !data.auto_route.includes(model.id) && model.is_enabled)
                .map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name} — {model.provider_label}
                  </option>
                ))}
            </select>
          </div>
        </Card>

        <Card title="Fallback and retry" subtitle="Applies to every request">
          <dl className="space-y-2 text-xs">
            <div className="flex justify-between">
              <dt className="text-muted">Max attempts per request</dt>
              <dd className="mono">{data.max_attempts}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Retry on</dt>
              <dd>429, 5xx, timeout, empty reply</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Circuit breaker</dt>
              <dd>3 failures / 60s → 30s cool-down</dd>
            </div>
          </dl>

          <div className="mt-4 pt-4 border-t border-border space-y-3">
            <Toggle
              checked={data.cache_enabled}
              onChange={(value) => saveSettings({ cache_enabled: value })}
              label="Response cache"
              description="Caches temperature-0 requests per partner. Cached replies cost nothing."
              disabled={saving}
            />
            <Field label="Cache TTL (seconds)">
              <input
                className="input mono h-8 text-xs"
                type="number"
                min="0"
                defaultValue={data.cache_ttl_seconds}
                onBlur={(event) =>
                  saveSettings({ cache_ttl_seconds: Number(event.target.value) || 0 })
                }
              />
            </Field>
          </div>
        </Card>
      </div>

      <Card
        title="Rules"
        subtitle="Evaluated top to bottom; the first match reorders the candidates"
        action={
          <button className="btn-secondary btn-sm" onClick={() => setRuleModal({})}>
            Add rule
          </button>
        }
        bodyClass="p-0"
      >
        {data.rules.length === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-muted">
            No rules. Requests go straight to the auto route order.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {data.rules.map((rule) => (
              <li key={rule.id} className="flex items-start gap-3 px-4 py-3">
                <Badge tone={rule.is_active ? 'accent' : 'neutral'}>
                  {rule.is_active ? 'on' : 'off'}
                </Badge>
                <div className="min-w-0 flex-1 text-xs">
                  <div>
                    <span className="text-muted">When </span>
                    <code className="mono">
                      {Object.entries(rule.condition)
                        .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
                        .join(' and ') || '—'}
                    </code>
                  </div>
                  <div className="mt-1">
                    <span className="text-muted">Then </span>
                    <code className="mono">{JSON.stringify(rule.action)}</code>
                  </div>
                </div>
                <div className="flex gap-1">
                  <button className="btn-ghost btn-sm" onClick={() => setRuleModal(rule)}>
                    Edit
                  </button>
                  <button
                    className="btn-ghost btn-sm text-danger"
                    onClick={() => deleteRule(rule)}
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Test routing"
        subtitle="See which models a sample request would try, without calling a provider"
        action={
          <button className="btn-secondary btn-sm" onClick={runPreview} disabled={previewBusy}>
            {previewBusy ? 'Testing…' : 'Run test'}
          </button>
        }
      >
        {!preview ? (
          <p className="text-xs text-muted">
            Runs the router with a plain text message and <code className="mono">model: "auto"</code>.
          </p>
        ) : preview.error ? (
          <p className="text-xs text-danger">{preview.error}</p>
        ) : (
          <ol className="space-y-1.5">
            {preview.candidates.map((candidate, index) => (
              <li key={candidate.model_id} className="flex items-center gap-2 text-xs">
                <span className="mono text-muted w-5">{index + 1}</span>
                <span className="mono">{candidate.model}</span>
                <span className="text-muted">via {candidate.provider}</span>
                {candidate.free_tier_provider && <Badge tone="neutral">free</Badge>}
                <span className="ml-auto mono text-muted">
                  {money(candidate.input_price_per_m)}/1M in
                </span>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <RuleDialog
        rule={ruleModal}
        catalog={data.catalog}
        onClose={() => setRuleModal(null)}
        onSave={saveRule}
      />
    </div>
  )
}

function RuleDialog({ rule, catalog, onClose, onSave }) {
  const [conditionKey, setConditionKey] = useState('has_image')
  const [conditionValue, setConditionValue] = useState('true')
  const [actionKey, setActionKey] = useState('prefer_provider')
  const [actionValue, setActionValue] = useState('')
  const [active, setActive] = useState(true)

  useEffect(() => {
    if (!rule) return
    const condition = Object.entries(rule.condition || {})[0]
    const action = Object.entries(rule.action || {})[0]
    setConditionKey(condition?.[0] || 'has_image')
    setConditionValue(condition ? String(condition[1]) : 'true')
    setActionKey(action?.[0] || 'prefer_provider')
    setActionValue(action ? JSON.stringify(action[1]) : '')
    setActive(rule.is_active ?? true)
  }, [rule])

  if (!rule) return null

  const submit = () => {
    let parsedCondition = conditionValue
    if (conditionValue === 'true') parsedCondition = true
    else if (conditionValue === 'false') parsedCondition = false
    else if (!Number.isNaN(Number(conditionValue)) && conditionValue !== '') {
      parsedCondition = Number(conditionValue)
    }

    let parsedAction
    try {
      parsedAction = JSON.parse(actionValue)
    } catch {
      parsedAction = actionValue
    }

    onSave({
      id: rule.id,
      position: rule.position ?? 0,
      condition: { [conditionKey]: parsedCondition },
      action: { [actionKey]: parsedAction },
      is_active: active,
    })
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={rule.id ? 'Edit rule' : 'Add rule'}
      description="Rules run in order. A rule whose condition matches reorders the candidate list."
      footer={
        <>
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submit}>
            Save rule
          </button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="When condition is">
          <select
            className="select"
            value={conditionKey}
            onChange={(event) => setConditionKey(event.target.value)}
          >
            {Object.keys(CONDITION_HELP).map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Value" hint={CONDITION_HELP[conditionKey]}>
          <input
            className="input mono"
            value={conditionValue}
            onChange={(event) => setConditionValue(event.target.value)}
          />
        </Field>

        <Field label="Then apply">
          <select
            className="select"
            value={actionKey}
            onChange={(event) => setActionKey(event.target.value)}
          >
            {Object.keys(ACTION_HELP).map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Value"
          hint={
            actionKey === 'prefer_provider'
              ? 'provider name, e.g. groq'
              : actionKey === 'use_models'
                ? 'JSON list of model ids, e.g. [1,2]'
                : 'JSON list, e.g. ["groq"]'
          }
        >
          <input
            className="input mono"
            value={actionValue}
            onChange={(event) => setActionValue(event.target.value)}
            placeholder={actionKey === 'use_models' ? '[1,2]' : 'groq'}
          />
        </Field>

        <div className="sm:col-span-2">
          <Toggle checked={active} onChange={setActive} label="Active" />
        </div>

        {actionKey === 'use_models' && (
          <div className="sm:col-span-2 rounded-md border border-border bg-surface p-2 max-h-40 overflow-y-auto">
            {catalog.slice(0, 40).map((model) => (
              <div key={model.id} className="flex items-center gap-2 text-xs py-0.5">
                <span className="mono text-muted w-8">{model.id}</span>
                <span className="truncate">{model.name}</span>
                <span className="text-muted ml-auto">{model.provider}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  )
}
