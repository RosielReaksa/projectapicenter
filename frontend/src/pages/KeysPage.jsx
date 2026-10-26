import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { datetime, money, number, relative } from '../lib/format'
import { useToast } from '../lib/toast'
import {
  Badge,
  ConfirmDialog,
  CopyButton,
  EmptyState,
  ErrorBox,
  Field,
  Modal,
  Spinner,
  TimeCell,
} from '../components/ui'

const EMPTY = {
  name: '',
  partner_id: '',
  daily_limit: '',
  monthly_cap_usd: '',
  rpm_limit: '',
  tpm_limit: '',
  concurrency_limit: '',
  allowed_models: '',
  allowed_ips: '',
  expires_at: '',
}

function toNumber(value) {
  if (value === '' || value === null || value === undefined) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function toList(value) {
  const items = value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  return items.length ? items : null
}

export default function KeysPage() {
  const toast = useToast()
  const [keys, setKeys] = useState(null)
  const [partners, setPartners] = useState([])
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(false)
  const [revealed, setRevealed] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    try {
      const [keyData, lookup] = await Promise.all([
        api.get('/admin/keys'),
        api.get('/admin/lookup'),
      ])
      setKeys(keyData.keys)
      setPartners(lookup.partners)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    try {
      const payload = {
        name: form.name.trim(),
        partner_id: form.partner_id ? Number(form.partner_id) : partners[0]?.id,
        daily_limit: toNumber(form.daily_limit),
        monthly_cap_usd: toNumber(form.monthly_cap_usd),
        rpm_limit: toNumber(form.rpm_limit),
        tpm_limit: toNumber(form.tpm_limit),
        concurrency_limit: toNumber(form.concurrency_limit),
        allowed_models: toList(form.allowed_models),
        allowed_ips: toList(form.allowed_ips),
        expires_at: form.expires_at || null,
      }
      const created = await api.post('/admin/keys', payload)
      setCreating(false)
      setForm(EMPTY)
      setRevealed(created)
      await load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (key) => {
    try {
      await api.post(`/admin/keys/${key.id}/${key.is_active ? 'disable' : 'enable'}`)
      toast.success(key.is_active ? 'Key disabled' : 'Key enabled')
      load()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.del(`/admin/keys/${confirm.id}`)
      toast.success('Key deleted')
      setConfirm(null)
      load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (error) return <ErrorBox error={error} onRetry={load} />

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">API Keys</h1>
          <p className="text-xs text-muted mt-1">
            Each key belongs to one partner and can be limited and revoked instantly.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          Create key
        </button>
      </header>

      <div className="card overflow-hidden">
        {!keys ? (
          <Spinner label="Loading keys" />
        ) : keys.length === 0 ? (
          <EmptyState
            title="No API keys yet"
            description="Create a key for each website or project that will call the gateway."
            action={
              <button className="btn-primary btn-sm" onClick={() => setCreating(true)}>
                Create your first key
              </button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Partner</th>
                  <th className="th">Key</th>
                  <th className="th text-right">Today</th>
                  <th className="th text-right">Month</th>
                  <th className="th">Limits</th>
                  <th className="th">Last used</th>
                  <th className="th">Status</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id} className="hover:bg-surface">
                    <td className="td">
                      <div className="font-medium">{key.name}</div>
                      <div className="text-xs text-muted mono">{key.prefix}…</div>
                    </td>
                    <td className="td text-xs">{key.partner_name}</td>
                    <td className="td">
                      <span className="mono text-xs text-muted">{key.prefix}…</span>
                    </td>
                    <td className="td text-right mono text-xs">
                      {number(key.requests_today)}
                      {key.daily_limit ? (
                        <span className="text-muted"> / {number(key.daily_limit)}</span>
                      ) : null}
                    </td>
                    <td className="td text-right mono text-xs">
                      {money(key.cost_this_month)}
                      {key.monthly_cap_usd ? (
                        <span className="text-muted"> / {money(key.monthly_cap_usd)}</span>
                      ) : null}
                    </td>
                    <td className="td">
                      <div className="flex flex-wrap gap-1">
                        {key.rpm_limit ? <Badge tone="neutral">{key.rpm_limit} rpm</Badge> : null}
                        {key.concurrency_limit ? (
                          <Badge tone="neutral">{key.concurrency_limit} conc</Badge>
                        ) : null}
                        {key.allowed_models ? <Badge tone="accent">model lock</Badge> : null}
                        {key.allowed_ips ? <Badge tone="accent">ip lock</Badge> : null}
                        {!key.rpm_limit && !key.concurrency_limit && !key.allowed_models && !key.allowed_ips && (
                          <span className="text-xs text-muted">Partner defaults</span>
                        )}
                      </div>
                    </td>
                    <td className="td">
                      <TimeCell value={key.last_used_at} />
                    </td>
                    <td className="td">
                      <Badge tone={key.is_active ? 'success' : 'neutral'}>
                        {key.is_active ? 'Active' : 'Disabled'}
                      </Badge>
                    </td>
                    <td className="td">
                      <div className="flex items-center justify-end gap-1">
                        <button className="btn-ghost btn-sm" onClick={() => toggle(key)}>
                          {key.is_active ? 'Disable' : 'Enable'}
                        </button>
                        <button
                          className="btn-ghost btn-sm text-danger"
                          onClick={() => setConfirm(key)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create dialog */}
      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="Create API key"
        description="The full key is shown once, right after you create it."
        width="max-w-2xl"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setCreating(false)} disabled={busy}>
              Cancel
            </button>
            <button className="btn-primary" onClick={submit} disabled={busy || !form.name.trim()}>
              {busy ? 'Creating…' : 'Create key'}
            </button>
          </>
        }
      >
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2">
            <input
              className="input"
              placeholder="dubber-website-prod"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              required
              autoFocus
            />
          </Field>

          <Field label="Partner" className="sm:col-span-2">
            <select
              className="select"
              value={form.partner_id || partners[0]?.id || ''}
              onChange={(event) => setForm({ ...form, partner_id: event.target.value })}
            >
              {partners.map((partner) => (
                <option key={partner.id} value={partner.id}>
                  {partner.name} ({partner.slug})
                </option>
              ))}
            </select>
          </Field>

          <Field label="Daily request limit" hint="Blank = partner default">
            <input
              className="input mono"
              type="number"
              min="0"
              value={form.daily_limit}
              onChange={(event) => setForm({ ...form, daily_limit: event.target.value })}
            />
          </Field>

          <Field label="Monthly spend cap (USD)" hint="Blank = partner default">
            <input
              className="input mono"
              type="number"
              min="0"
              step="0.01"
              value={form.monthly_cap_usd}
              onChange={(event) => setForm({ ...form, monthly_cap_usd: event.target.value })}
            />
          </Field>

          <Field label="Requests per minute" hint="Blank = partner default">
            <input
              className="input mono"
              type="number"
              min="1"
              value={form.rpm_limit}
              onChange={(event) => setForm({ ...form, rpm_limit: event.target.value })}
            />
          </Field>

          <Field label="Concurrency cap" hint="Max requests in flight">
            <input
              className="input mono"
              type="number"
              min="1"
              value={form.concurrency_limit}
              onChange={(event) => setForm({ ...form, concurrency_limit: event.target.value })}
            />
          </Field>

          <Field label="Tokens per minute" hint="Blank = partner default">
            <input
              className="input mono"
              type="number"
              min="1"
              value={form.tpm_limit}
              onChange={(event) => setForm({ ...form, tpm_limit: event.target.value })}
            />
          </Field>

          <Field label="Expires at" hint="Optional">
            <input
              className="input"
              type="datetime-local"
              value={form.expires_at}
              onChange={(event) => setForm({ ...form, expires_at: event.target.value })}
            />
          </Field>

          <Field
            label="Allowed models"
            hint="Comma separated. Blank = all models."
            className="sm:col-span-2"
          >
            <input
              className="input mono"
              placeholder="gemini-2.5-flash, llama-3.3-70b-versatile"
              value={form.allowed_models}
              onChange={(event) => setForm({ ...form, allowed_models: event.target.value })}
            />
          </Field>

          <Field
            label="Allowed IPs"
            hint="Comma separated. Blank = any IP."
            className="sm:col-span-2"
          >
            <input
              className="input mono"
              placeholder="203.0.113.10"
              value={form.allowed_ips}
              onChange={(event) => setForm({ ...form, allowed_ips: event.target.value })}
            />
          </Field>
        </form>
      </Modal>

      {/* Reveal-once dialog */}
      <Modal
        open={!!revealed}
        onClose={() => setRevealed(null)}
        title="Your new API key"
        description="Copy it now. It cannot be shown again."
        footer={
          <button className="btn-primary" onClick={() => setRevealed(null)}>
            I've saved it
          </button>
        }
      >
        <div className="space-y-3">
          <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2.5">
            <code className="mono text-xs break-all flex-1">{revealed?.key}</code>
            <CopyButton
              value={revealed?.key || ''}
              onCopied={() => toast.success('Key copied to clipboard')}
            />
          </div>
          <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
            Store this in your server's environment variables. Never put it in browser JavaScript.
          </div>
          <pre className="rounded-md bg-surface border border-border p-3 overflow-x-auto">
            <code className="mono text-xs">{`curl ${window.location.origin}/v1/chat/completions \\
  -H "Authorization: Bearer ${revealed?.key || 'sk-gw-...'}" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello"}]}'`}</code>
          </pre>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={remove}
        busy={busy}
        title="Delete this key?"
        confirmLabel="Delete key"
        message={`"${confirm?.name}" will stop working immediately. Requests using it will get a 401. This cannot be undone.`}
      />
    </div>
  )
}
