import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { ms, relative } from '../lib/format'
import { useToast } from '../lib/toast'
import {
  Badge,
  Card,
  ConfirmDialog,
  ErrorBox,
  Field,
  Modal,
  Spinner,
  StatusDot,
  Toggle,
} from '../components/ui'

const KNOWN = [
  { name: 'groq', label: 'Groq' },
  { name: 'gemini', label: 'Google Gemini' },
  { name: 'openrouter', label: 'OpenRouter' },
  { name: 'deepseek', label: 'DeepSeek' },
  { name: 'mistral', label: 'Mistral' },
  { name: 'cerebras', label: 'Cerebras' },
  { name: 'together', label: 'Together AI' },
  { name: 'openai', label: 'OpenAI' },
  { name: 'custom', label: 'Custom (OpenAI-compatible)' },
]

export default function ProvidersPage() {
  const toast = useToast()
  const [providers, setProviders] = useState(null)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    try {
      const data = await api.get('/admin/providers')
      setProviders(data.providers)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const save = async (payload, id) => {
    setBusy(true)
    try {
      if (id) {
        await api.patch(`/admin/providers/${id}`, payload)
        toast.success('Provider updated')
      } else {
        await api.post('/admin/providers', payload)
        toast.success('Provider added')
      }
      setEditing(null)
      setAdding(false)
      await load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const test = async (provider) => {
    setTesting(provider.id)
    try {
      const result = await api.post(`/admin/providers/${provider.id}/test`)
      if (result.ok) toast.success(`${provider.label}: connected in ${ms(result.latency_ms)}`)
      else toast.error(`${provider.label}: ${result.error || 'connection failed'}`)
      load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setTesting(null)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.del(`/admin/providers/${confirm.id}`)
      toast.success('Provider removed')
      setConfirm(null)
      load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const quickToggle = async (provider, patch) => {
    try {
      await api.patch(`/admin/providers/${provider.id}`, patch)
      load()
    } catch (err) {
      toast.error(err.message)
    }
  }

  if (error) return <ErrorBox error={error} onRetry={load} />

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Providers</h1>
          <p className="text-xs text-muted mt-1">
            Upstream accounts. Keys are encrypted at rest and only ever shown masked.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setAdding(true)}>
          Add provider
        </button>
      </header>

      {!providers ? (
        <Spinner label="Loading providers" />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {providers.map((provider) => (
            <Card key={provider.id} bodyClass="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium truncate">{provider.label}</h3>
                    {provider.free_tier && <Badge tone="neutral">free tier</Badge>}
                    {!provider.is_active && <Badge tone="neutral">disabled</Badge>}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <StatusDot status={provider.health_status} />
                    <span className="text-xs text-muted">
                      {provider.last_checked_at ? relative(provider.last_checked_at) : 'never checked'}
                    </span>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="mono text-xs">{provider.last_latency_ms ? ms(provider.last_latency_ms) : '—'}</div>
                  <div className="text-[10px] text-muted">latency</div>
                </div>
              </div>

              <div className="mt-3 space-y-1.5 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="text-muted">API key</span>
                  <span className="mono truncate">
                    {provider.has_key ? provider.key_hint : 'not set'}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted">Models</span>
                  <span className="mono">
                    {provider.enabled_model_count}/{provider.model_count}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted">Priority</span>
                  <span className="mono">{provider.priority}</span>
                </div>
                {provider.monthly_cap_usd != null && (
                  <div className="flex justify-between gap-2">
                    <span className="text-muted">Monthly cap</span>
                    <span className="mono">${provider.monthly_cap_usd.toFixed(2)}</span>
                  </div>
                )}
              </div>

              {provider.last_error && (
                <p className="mt-2 text-xs text-danger/90 line-clamp-2" title={provider.last_error}>
                  {provider.last_error}
                </p>
              )}

              {provider.may_train_on_prompts && (
                <p className="mt-2 text-[11px] text-warning">
                  May use prompts for training — do not route private partner data here.
                </p>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-1 border-t border-border pt-3">
                <button
                  className="btn-secondary btn-sm"
                  onClick={() => test(provider)}
                  disabled={!provider.has_key || testing === provider.id}
                >
                  {testing === provider.id ? 'Testing…' : 'Test'}
                </button>
                <button className="btn-ghost btn-sm" onClick={() => setEditing(provider)}>
                  Edit
                </button>
                <button
                  className="btn-ghost btn-sm"
                  onClick={() => quickToggle(provider, { is_active: !provider.is_active })}
                >
                  {provider.is_active ? 'Disable' : 'Enable'}
                </button>
                <button
                  className="btn-ghost btn-sm text-danger ml-auto"
                  onClick={() => setConfirm(provider)}
                >
                  Remove
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <ProviderDialog
        open={adding || !!editing}
        provider={editing}
        onClose={() => {
          setAdding(false)
          setEditing(null)
        }}
        onSave={save}
        busy={busy}
      />

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={remove}
        busy={busy}
        title="Remove provider?"
        confirmLabel="Remove provider"
        message={`${confirm?.label} and its models will be deleted. Requests routed to it will fail over to other providers.`}
      />
    </div>
  )
}

function ProviderDialog({ open, provider, onClose, onSave, busy }) {
  const isNew = !provider
  const [form, setForm] = useState({})
  const [available, setAvailable] = useState(KNOWN)

  useEffect(() => {
    if (!open) return
    setForm({
      name: provider?.name || 'groq',
      label: provider?.label || '',
      base_url: provider?.base_url || '',
      api_key: '',
      is_free_tier: provider?.is_free_tier ?? false,
      may_train_on_prompts: provider?.may_train_on_prompts ?? false,
      monthly_cap_usd: provider?.monthly_cap_usd ?? '',
      rpm_limit: provider?.rpm_limit ?? '',
      priority: provider?.priority ?? 100,
      is_active: provider?.is_active ?? true,
    })
  }, [open, provider])

  const submit = (event) => {
    event.preventDefault()
    const payload = {
      label: form.label || undefined,
      base_url: form.base_url || undefined,
      is_free_tier: form.is_free_tier,
      may_train_on_prompts: form.may_train_on_prompts,
      monthly_cap_usd: form.monthly_cap_usd === '' ? null : Number(form.monthly_cap_usd),
      rpm_limit: form.rpm_limit === '' ? null : Number(form.rpm_limit),
      priority: Number(form.priority),
      is_active: form.is_active,
    }
    if (form.api_key.trim()) payload.api_key = form.api_key.trim()
    if (isNew) payload.name = form.name
    onSave(payload, provider?.id)
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isNew ? 'Add provider' : `Edit ${provider?.label}`}
      width="max-w-xl"
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? 'Saving…' : isNew ? 'Add provider' : 'Save changes'}
          </button>
        </>
      }
    >
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        {isNew && (
          <Field label="Provider" className="sm:col-span-2">
            <select
              className="select"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            >
              {available.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
        )}

        <Field label="Display name" className="sm:col-span-2">
          <input
            className="input"
            placeholder="Groq (production)"
            value={form.label}
            onChange={(event) => setForm({ ...form, label: event.target.value })}
          />
        </Field>

        <Field
          label="Base URL"
          hint="Leave blank to use the default for this provider"
          className="sm:col-span-2"
        >
          <input
            className="input mono text-xs"
            placeholder="https://api.groq.com/openai/v1"
            value={form.base_url}
            onChange={(event) => setForm({ ...form, base_url: event.target.value })}
          />
        </Field>

        <Field
          label={isNew ? 'API key' : 'Replace API key'}
          hint={isNew ? 'Stored encrypted' : 'Leave blank to keep the current key'}
          className="sm:col-span-2"
        >
          <input
            className="input mono text-xs"
            type="password"
            autoComplete="off"
            placeholder="sk-..."
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
          />
        </Field>

        <Field label="Priority" hint="Lower is tried first">
          <input
            className="input mono"
            type="number"
            value={form.priority}
            onChange={(event) => setForm({ ...form, priority: event.target.value })}
          />
        </Field>

        <Field label="Requests per minute" hint="Provider-side limit">
          <input
            className="input mono"
            type="number"
            min="0"
            value={form.rpm_limit}
            onChange={(event) => setForm({ ...form, rpm_limit: event.target.value })}
          />
        </Field>

        <Field label="Monthly spend cap (USD)" hint="Blank = no cap">
          <input
            className="input mono"
            type="number"
            min="0"
            step="0.01"
            value={form.monthly_cap_usd}
            onChange={(event) => setForm({ ...form, monthly_cap_usd: event.target.value })}
          />
        </Field>

        <div className="space-y-3 sm:col-span-2 pt-1">
          <Toggle
            checked={form.is_free_tier}
            onChange={(value) => setForm({ ...form, is_free_tier: value })}
            label="Free tier"
            description="Free tiers are used only as a last-resort fallback, and are disabled per partner by default."
          />
          <Toggle
            checked={form.may_train_on_prompts}
            onChange={(value) => setForm({ ...form, may_train_on_prompts: value })}
            label="May train on prompts"
            description="Flags this provider so private partner data is not routed here."
          />
          <Toggle
            checked={form.is_active}
            onChange={(value) => setForm({ ...form, is_active: value })}
            label="Active"
            description="Disabled providers are skipped by the router."
          />
        </div>
      </form>
    </Modal>
  )
}
