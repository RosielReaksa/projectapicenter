import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { datetime, money, number } from '../lib/format'
import { useToast } from '../lib/toast'
import {
  Badge,
  Card,
  ConfirmDialog,
  ErrorBox,
  Field,
  Modal,
  Spinner,
  Toggle,
} from '../components/ui'

const EMPTY = {
  name: '',
  concurrency_limit: 10,
  rpm_limit: 120,
  tpm_limit: '',
  daily_request_limit: '',
  monthly_cap_usd: 20,
  over_capacity: 'reject',
  queue_timeout_ms: 2000,
  markup_multiplier: 1.0,
  allow_free_tier: false,
  notes: '',
  login_email: '',
  login_password: '',
}

function nullIfBlank(value) {
  return value === '' || value === null || value === undefined ? null : Number(value)
}

export default function PartnersPage() {
  const toast = useToast()
  const location = useLocation()
  const navigate = useNavigate()
  const [partners, setPartners] = useState(null)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    try {
      const data = await api.get('/admin/partners')
      setPartners(data.partners)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Prefilled from an approved access request (HOMEPAGE_SPEC §4.4).
  useEffect(() => {
    const prefill = location.state?.prefill
    if (!prefill) return
    setEditing(null)
    setForm({
      ...EMPTY,
      name: prefill.name || '',
      notes: prefill.notes || '',
      login_email: prefill.login_email || '',
    })
    setCreating(true)
    navigate(location.pathname, { replace: true, state: null })
  }, [location.state, location.pathname, navigate])

  const openCreate = () => {
    setForm(EMPTY)
    setEditing(null)
    setCreating(true)
  }

  const openEdit = (partner) => {
    setForm({
      name: partner.name,
      concurrency_limit: partner.concurrency_limit ?? '',
      rpm_limit: partner.rpm_limit ?? '',
      tpm_limit: partner.tpm_limit ?? '',
      daily_request_limit: partner.daily_request_limit ?? '',
      monthly_cap_usd: partner.monthly_cap_usd ?? '',
      over_capacity: partner.over_capacity,
      queue_timeout_ms: partner.queue_timeout_ms,
      markup_multiplier: partner.markup_multiplier,
      allow_free_tier: partner.allow_free_tier,
      notes: partner.notes || '',
      login_email: '',
      login_password: '',
    })
    setEditing(partner)
    setCreating(true)
  }

  const submit = async () => {
    setBusy(true)
    try {
      const payload = {
        name: form.name.trim(),
        concurrency_limit: nullIfBlank(form.concurrency_limit),
        rpm_limit: nullIfBlank(form.rpm_limit),
        tpm_limit: nullIfBlank(form.tpm_limit),
        daily_request_limit: nullIfBlank(form.daily_request_limit),
        monthly_cap_usd: nullIfBlank(form.monthly_cap_usd),
        over_capacity: form.over_capacity,
        queue_timeout_ms: Number(form.queue_timeout_ms),
        markup_multiplier: Number(form.markup_multiplier),
        allow_free_tier: form.allow_free_tier,
        notes: form.notes || null,
      }
      if (editing) {
        await api.patch(`/admin/partners/${editing.id}`, payload)
        toast.success('Partner updated')
      } else {
        if (form.login_email) {
          payload.login_email = form.login_email
          payload.login_password = form.login_password
        }
        await api.post('/admin/partners', payload)
        toast.success('Partner created')
      }
      setCreating(false)
      await load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleActive = async (partner) => {
    try {
      await api.patch(`/admin/partners/${partner.id}`, { is_active: !partner.is_active })
      toast.success(partner.is_active ? 'Partner deactivated' : 'Partner activated')
      load()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.del(`/admin/partners/${confirm.id}`)
      toast.success('Partner deleted')
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
          <h1 className="page-title">Partners</h1>
          <p className="text-xs text-muted mt-1">
            A partner owns one or more keys and carries its own limits, markup and usage view.
          </p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          Add partner
        </button>
      </header>

      {!partners ? (
        <Spinner label="Loading partners" />
      ) : partners.length === 0 ? (
        <Card>
          <p className="text-sm text-center py-6 text-muted">No partners yet.</p>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {partners.map((partner) => (
            <Card key={partner.id} bodyClass="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium truncate">{partner.name}</h3>
                    <Badge tone={partner.is_active ? 'success' : 'neutral'}>
                      {partner.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted mono mt-0.5">{partner.slug}</div>
                </div>
                <div className="text-right">
                  <div className="mono text-sm">{partner.key_count ?? 0}</div>
                  <div className="text-[10px] text-muted">keys</div>
                </div>
              </div>

              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <Row label="Concurrency" value={partner.concurrency_limit ?? '∞'} />
                <Row label="RPM" value={partner.rpm_limit ?? '∞'} />
                <Row label="TPM" value={partner.tpm_limit ? number(partner.tpm_limit) : '∞'} />
                <Row
                  label="Daily requests"
                  value={partner.daily_request_limit ?? '∞'}
                />
                <Row
                  label="Monthly cap"
                  value={partner.monthly_cap_usd != null ? money(partner.monthly_cap_usd) : '∞'}
                />
                <Row label="Markup" value={`×${partner.markup_multiplier}`} />
              </dl>

              <div className="mt-2 flex flex-wrap gap-1">
                {partner.allow_free_tier ? (
                  <Badge tone="neutral">free tier allowed</Badge>
                ) : (
                  <Badge tone="accent">paid only</Badge>
                )}
                <Badge tone="neutral">on {partner.over_capacity}</Badge>
              </div>

              {partner.notes && (
                <p className="mt-2 text-xs text-muted line-clamp-2">{partner.notes}</p>
              )}

              <div className="mt-3 flex gap-1 border-t border-border pt-3">
                <button className="btn-ghost btn-sm" onClick={() => openEdit(partner)}>
                  Edit
                </button>
                <button className="btn-ghost btn-sm" onClick={() => toggleActive(partner)}>
                  {partner.is_active ? 'Deactivate' : 'Activate'}
                </button>
                <button
                  className="btn-ghost btn-sm text-danger ml-auto"
                  onClick={() => setConfirm(partner)}
                >
                  Delete
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title={editing ? `Edit ${editing.name}` : 'Add partner'}
        description={
          editing
            ? 'Deactivating a partner disables all of its keys.'
            : 'Limits are optional. Blank means unlimited.'
        }
        width="max-w-2xl"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setCreating(false)} disabled={busy}>
              Cancel
            </button>
            <button className="btn-primary" onClick={submit} disabled={busy || !form.name.trim()}>
              {busy ? 'Saving…' : editing ? 'Save changes' : 'Create partner'}
            </button>
          </>
        }
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2">
            <input
              className="input"
              placeholder="Acme Websites"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              autoFocus
            />
          </Field>

          <Field label="Concurrency cap" hint="Requests in flight">
            <input
              className="input mono"
              type="number"
              min="1"
              value={form.concurrency_limit}
              onChange={(event) => setForm({ ...form, concurrency_limit: event.target.value })}
            />
          </Field>

          <Field label="Requests per minute">
            <input
              className="input mono"
              type="number"
              min="1"
              value={form.rpm_limit}
              onChange={(event) => setForm({ ...form, rpm_limit: event.target.value })}
            />
          </Field>

          <Field label="Tokens per minute">
            <input
              className="input mono"
              type="number"
              min="1"
              value={form.tpm_limit}
              onChange={(event) => setForm({ ...form, tpm_limit: event.target.value })}
            />
          </Field>

          <Field label="Daily request limit">
            <input
              className="input mono"
              type="number"
              min="0"
              value={form.daily_request_limit}
              onChange={(event) => setForm({ ...form, daily_request_limit: event.target.value })}
            />
          </Field>

          <Field label="Monthly spend cap (USD)" hint="Provider cost">
            <input
              className="input mono"
              type="number"
              min="0"
              step="0.01"
              value={form.monthly_cap_usd}
              onChange={(event) => setForm({ ...form, monthly_cap_usd: event.target.value })}
            />
          </Field>

          <Field label="Markup multiplier" hint="Price = cost × markup">
            <input
              className="input mono"
              type="number"
              min="0"
              step="0.05"
              value={form.markup_multiplier}
              onChange={(event) => setForm({ ...form, markup_multiplier: event.target.value })}
            />
          </Field>

          <Field label="When over capacity">
            <select
              className="select"
              value={form.over_capacity}
              onChange={(event) => setForm({ ...form, over_capacity: event.target.value })}
            >
              <option value="reject">Reject fast (429)</option>
              <option value="queue">Wait briefly, then reject</option>
            </select>
          </Field>

          <Field label="Queue timeout (ms)" hint="Only used when queueing">
            <input
              className="input mono"
              type="number"
              min="0"
              max="30000"
              value={form.queue_timeout_ms}
              onChange={(event) => setForm({ ...form, queue_timeout_ms: event.target.value })}
            />
          </Field>

          <div className="sm:col-span-2">
            <Toggle
              checked={form.allow_free_tier}
              onChange={(value) => setForm({ ...form, allow_free_tier: value })}
              label="Allow free-tier providers"
              description="Off by default: free tiers can change without notice and may train on prompts."
            />
          </div>

          <Field label="Notes" className="sm:col-span-2">
            <textarea
              className="textarea text-xs"
              rows={2}
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>

          {!editing && (
            <>
              <Field label="Console login email" hint="Optional — lets the partner see its own usage">
                <input
                  className="input"
                  type="email"
                  value={form.login_email}
                  onChange={(event) => setForm({ ...form, login_email: event.target.value })}
                />
              </Field>
              <Field label="Login password" hint="At least 10 characters">
                <input
                  className="input mono text-xs"
                  type="password"
                  value={form.login_password}
                  onChange={(event) => setForm({ ...form, login_password: event.target.value })}
                />
              </Field>
            </>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={remove}
        busy={busy}
        title="Delete partner?"
        confirmLabel="Delete partner"
        message={`${confirm?.name} and all of its keys will be deleted. Requests using those keys will start failing immediately.`}
      />
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted">{label}</dt>
      <dd className="mono">{value}</dd>
    </div>
  )
}
