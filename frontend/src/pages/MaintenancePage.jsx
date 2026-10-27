import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { datetime, relative } from '../lib/format'
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

const CONFIRM_WORD = 'MAINTENANCE'

export default function MaintenancePage() {
  const toast = useToast()
  const [state, setState] = useState(null)
  const [history, setHistory] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const [startOpen, setStartOpen] = useState(false)
  const [startForm, setStartForm] = useState({
    message: 'We are upgrading. Back soon.',
    expected_end: '',
    reason: '',
    confirm: '',
  })
  const [degradedForm, setDegradedForm] = useState({
    paid_only: false,
    free_only: false,
    reduce: false,
    reduce_pct: 50,
    max_tokens_on: false,
    max_tokens: 2000,
  })
  const [suspendTarget, setSuspendTarget] = useState(null)
  const [suspendForm, setSuspendForm] = useState({ internal: '', public: '' })
  const [stopConfirm, setStopConfirm] = useState(false)
  const [windowOpen, setWindowOpen] = useState(false)
  const [windowForm, setWindowForm] = useState({
    starts_at: '',
    ends_at: '',
    banner_text: '',
    auto_enable: false,
  })

  const load = useCallback(async () => {
    try {
      const [data, log] = await Promise.all([
        api.get('/admin/maintenance'),
        api.get('/admin/maintenance/history?limit=50'),
      ])
      setState(data)
      setHistory(log.entries)
      setDegradedForm((current) => ({
        ...current,
        paid_only: data.degraded.paid_only,
        free_only: data.degraded.free_only,
        reduce: data.degraded.reduce_concurrency_pct > 0,
        reduce_pct: data.degraded.reduce_concurrency_pct || 50,
        max_tokens_on: Boolean(data.degraded.max_tokens),
        max_tokens: data.degraded.max_tokens || 2000,
      }))
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 15000)
    return () => window.clearInterval(timer)
  }, [load])

  const act = async (fn, successMessage) => {
    setBusy(true)
    try {
      await fn()
      if (successMessage) toast.success(successMessage)
      await load()
      return true
    } catch (err) {
      toast.error(err.message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const startMaintenance = async () => {
    const ok = await act(
      () =>
        api.post('/admin/maintenance/global/start', {
          message: startForm.message,
          expected_end: startForm.expected_end
            ? new Date(startForm.expected_end).toISOString()
            : null,
          reason: startForm.reason,
          allow_current_ip: true,
        }),
      'Maintenance started',
    )
    if (ok) {
      setStartOpen(false)
      setStartForm({ message: '', expected_end: '', reason: '', confirm: '' })
    }
  }

  const stopMaintenance = async () => {
    const ok = await act(
      () => api.post('/admin/maintenance/global/stop', { reason: 'stopped from dashboard' }),
      'Maintenance lifted',
    )
    if (ok) setStopConfirm(false)
  }

  const saveDegraded = async (active) => {
    await act(
      () =>
        api.post('/admin/maintenance/degraded', {
          active,
          paid_only: degradedForm.paid_only,
          free_only: degradedForm.free_only,
          reduce_concurrency_pct: degradedForm.reduce ? Number(degradedForm.reduce_pct) : 0,
          max_tokens: degradedForm.max_tokens_on ? Number(degradedForm.max_tokens) : null,
          reason: 'set from dashboard',
        }),
      active ? 'Degraded mode on' : 'Degraded mode off',
    )
  }

  const toggleProvider = (provider) =>
    act(
      () =>
        provider.paused
          ? api.post(`/admin/providers/${provider.id}/resume`)
          : api.post(`/admin/providers/${provider.id}/pause`, { reason: 'paused from dashboard' }),
      provider.paused ? `${provider.label} resumed` : `${provider.label} paused`,
    )

  const togglePartner = (partner) => {
    if (partner.suspended) {
      return act(
        () => api.post(`/admin/partners/${partner.id}/resume`),
        `${partner.name} resumed`,
      )
    }
    setSuspendForm({ internal: '', public: '' })
    setSuspendTarget(partner)
    return Promise.resolve()
  }

  const doSuspend = async () => {
    const ok = await act(
      () =>
        api.post(`/admin/partners/${suspendTarget.id}/suspend`, {
          reason_internal: suspendForm.internal,
          reason_public: suspendForm.public || null,
        }),
      `${suspendTarget.name} suspended`,
    )
    if (ok) setSuspendTarget(null)
  }

  const createWindow = async () => {
    const ok = await act(
      () =>
        api.post('/admin/maintenance/windows', {
          starts_at: new Date(windowForm.starts_at).toISOString(),
          ends_at: new Date(windowForm.ends_at).toISOString(),
          banner_text: windowForm.banner_text,
          auto_enable: windowForm.auto_enable,
        }),
      'Window scheduled',
    )
    if (ok) {
      setWindowOpen(false)
      setWindowForm({ starts_at: '', ends_at: '', banner_text: '', auto_enable: false })
    }
  }

  const cancelWindow = (id) =>
    act(() => api.del(`/admin/maintenance/windows/${id}`), 'Window cancelled')

  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!state) return <Spinner label="Loading maintenance state" />

  const active = state.global.active
  const degradedOn = state.degraded.active
  const pausedCount = state.providers.filter((p) => p.paused).length
  const suspendedCount = state.partners.filter((p) => p.suspended).length
  const overdue =
    active && state.global.expected_end && new Date(state.global.expected_end) < new Date()

  return (
    <div className="space-y-5">
      <header>
        <h1 className="page-title">Maintenance</h1>
        <p className="text-xs text-muted mt-1">
          Four levels of control, from pausing one provider to stopping all API calls.
        </p>
      </header>

      {state.stale && (
        <div className="card border-warning/40 px-4 py-3 text-sm">
          State may be stale: the shared store is unreachable. Showing the last known state from
          the database.
        </div>
      )}

      {/* Status banner */}
      <div
        className={`card px-4 py-3 text-sm flex items-center gap-3 ${
          active ? 'border-danger/50' : degradedOn || pausedCount ? 'border-warning/40' : ''
        }`}
      >
        <span
          className={`h-2 w-2 rounded-full shrink-0 ${
            active ? 'bg-danger' : degradedOn || pausedCount ? 'bg-warning' : 'bg-success'
          }`}
          aria-hidden="true"
        />
        <div className="flex-1">
          {active ? (
            <>
              <strong>Maintenance active</strong> since {relative(state.global.started_at)}
              {state.drain.draining
                ? ` · draining (${state.drain.in_flight} in flight)`
                : ' · drained'}
              {overdue && (
                <span className="text-danger"> · overdue (expected end has passed)</span>
              )}
            </>
          ) : degradedOn || pausedCount || suspendedCount ? (
            <>
              <strong>Degraded</strong>
              {degradedOn && ' · restrictions active'}
              {pausedCount > 0 && ` · ${pausedCount} provider${pausedCount > 1 ? 's' : ''} paused`}
              {suspendedCount > 0 && ` · ${suspendedCount} partner suspended`}
            </>
          ) : (
            <strong>All systems normal</strong>
          )}
        </div>
        {active && (
          <button className="btn-primary btn-sm" onClick={() => setStopConfirm(true)}>
            Lift maintenance
          </button>
        )}
      </div>

      {/* Full maintenance */}
      <Card
        title="Full maintenance"
        subtitle="Stops all API calls. Partners get a 503 with a retry time."
        className={active ? 'border-danger/40' : ''}
        action={
          active ? (
            <Badge tone="danger">ON</Badge>
          ) : (
            <button className="btn-danger btn-sm" onClick={() => setStartOpen(true)}>
              Start maintenance
            </button>
          )
        }
      >
        {active ? (
          <dl className="space-y-2 text-xs">
            <Row label="Message to partners">{state.global.message}</Row>
            <Row label="Started">{datetime(state.global.started_at)}</Row>
            <Row label="Expected end">
              {state.global.expected_end ? datetime(state.global.expected_end) : 'not set'}
            </Row>
            <Row label="Allowed IPs" mono>
              {state.global.allow_ips.join(', ') || 'none'}
            </Row>
            <Row label="Drain">
              {state.drain.drained
                ? 'complete'
                : `${state.drain.in_flight} in flight, ${state.drain.elapsed_seconds}s of ${state.drain.timeout_seconds}s`}
            </Row>
          </dl>
        ) : (
          <p className="text-xs text-muted">
            Normal deploys should use a rolling restart instead. Use this only when a change cannot
            run alongside the old version, such as a database migration.
          </p>
        )}
      </Card>

      {/* Degraded mode */}
      <Card
        title="Degraded mode"
        subtitle="Keep serving with restrictions"
        action={
          <Toggle
            checked={degradedOn}
            onChange={(value) => saveDegraded(value)}
            label=""
            disabled={busy}
          />
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Toggle
            checked={degradedForm.paid_only}
            onChange={(value) => setDegradedForm({ ...degradedForm, paid_only: value })}
            label="Paid routes only"
            description="Skip free-tier providers"
          />
          <Toggle
            checked={degradedForm.free_only}
            onChange={(value) => setDegradedForm({ ...degradedForm, free_only: value })}
            label="Free routes only"
            description="Stop all paid spend immediately"
          />
          <div className="space-y-2">
            <Toggle
              checked={degradedForm.reduce}
              onChange={(value) => setDegradedForm({ ...degradedForm, reduce: value })}
              label="Reduce concurrency"
            />
            {degradedForm.reduce && (
              <input
                className="input mono h-8 text-xs"
                type="number"
                min="1"
                max="100"
                value={degradedForm.reduce_pct}
                onChange={(event) =>
                  setDegradedForm({ ...degradedForm, reduce_pct: event.target.value })
                }
                aria-label="Concurrency reduction percent"
              />
            )}
          </div>
          <div className="space-y-2">
            <Toggle
              checked={degradedForm.max_tokens_on}
              onChange={(value) => setDegradedForm({ ...degradedForm, max_tokens_on: value })}
              label="Cap max tokens"
            />
            {degradedForm.max_tokens_on && (
              <input
                className="input mono h-8 text-xs"
                type="number"
                min="1"
                value={degradedForm.max_tokens}
                onChange={(event) =>
                  setDegradedForm({ ...degradedForm, max_tokens: event.target.value })
                }
                aria-label="Max tokens per request"
              />
            )}
          </div>
        </div>
        <button
          className="btn-secondary btn-sm mt-4"
          onClick={() => saveDegraded(degradedOn)}
          disabled={busy}
        >
          Apply restrictions
        </button>
      </Card>

      {/* Providers */}
      <Card title="Providers" subtitle="Pause one provider; traffic moves to the others" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Provider</th>
              <th className="th">Status</th>
              <th className="th text-right">Control</th>
            </tr>
          </thead>
          <tbody>
            {state.providers.map((provider) => (
              <tr key={provider.id}>
                <td className="td text-xs">{provider.label}</td>
                <td className="td">
                  {provider.paused ? (
                    <Badge tone="warning">Paused</Badge>
                  ) : (
                    <Badge tone={provider.health_status === 'healthy' ? 'success' : 'neutral'}>
                      {provider.health_status}
                    </Badge>
                  )}
                </td>
                <td className="td text-right">
                  <button
                    className="btn-ghost btn-sm"
                    onClick={() => toggleProvider(provider)}
                    disabled={busy}
                  >
                    {provider.paused ? 'Resume' : 'Pause'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Partners */}
      <Card title="Partners" subtitle="Suspension blocks only that partner's keys" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Partner</th>
              <th className="th">Status</th>
              <th className="th text-right">Control</th>
            </tr>
          </thead>
          <tbody>
            {state.partners.map((partner) => (
              <tr key={partner.id}>
                <td className="td text-xs">{partner.name}</td>
                <td className="td">
                  {partner.suspended ? (
                    <Badge tone="danger">Suspended</Badge>
                  ) : (
                    <Badge tone="success">Active</Badge>
                  )}
                </td>
                <td className="td text-right">
                  <button
                    className="btn-ghost btn-sm"
                    onClick={() => togglePartner(partner)}
                    disabled={busy}
                  >
                    {partner.suspended ? 'Resume' : 'Suspend'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Scheduled windows */}
      <Card
        title="Scheduled maintenance"
        subtitle="Announced on the public site before it starts"
        action={
          <button className="btn-secondary btn-sm" onClick={() => setWindowOpen(true)}>
            Schedule
          </button>
        }
        bodyClass="p-0"
      >
        {state.windows.length === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-muted">No scheduled windows.</p>
        ) : (
          <ul className="divide-y divide-border">
            {state.windows.map((window) => (
              <li key={window.id} className="flex items-start gap-3 px-4 py-3">
                <Badge tone={window.status === 'active' ? 'danger' : 'accent'}>
                  {window.status}
                </Badge>
                <div className="min-w-0 flex-1 text-xs">
                  <div>{window.banner_text}</div>
                  <div className="text-muted mono mt-1">
                    {datetime(window.starts_at)} → {datetime(window.ends_at)}
                    {window.auto_enable && ' · auto'}
                  </div>
                </div>
                <button
                  className="btn-ghost btn-sm text-danger"
                  onClick={() => cancelWindow(window.id)}
                  disabled={busy}
                >
                  Cancel
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* History */}
      <Card title="History" subtitle="Who changed what, when and why" bodyClass="p-0">
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Time</th>
                <th className="th">Source</th>
                <th className="th">Level</th>
                <th className="th">Action</th>
                <th className="th">Target</th>
                <th className="th">Reason</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td className="td text-xs text-muted" colSpan={6}>
                    No changes yet.
                  </td>
                </tr>
              ) : (
                history.map((entry) => (
                  <tr key={entry.id}>
                    <td className="td text-xs text-muted whitespace-nowrap">
                      {relative(entry.time)}
                    </td>
                    <td className="td text-xs">{entry.source}</td>
                    <td className="td text-xs">{entry.level}</td>
                    <td className="td text-xs mono">{entry.action}</td>
                    <td className="td text-xs">{entry.target || '—'}</td>
                    <td className="td text-xs text-muted truncate max-w-[220px]">
                      {entry.reason || '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Start confirmation — typed word required (§5.2) */}
      <Modal
        open={startOpen}
        onClose={() => setStartOpen(false)}
        title="Start full maintenance?"
        description="This will block all partner requests."
        width="max-w-lg"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setStartOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button
              className="btn-danger"
              onClick={startMaintenance}
              disabled={
                busy ||
                startForm.confirm !== CONFIRM_WORD ||
                !startForm.reason.trim() ||
                !startForm.message.trim()
              }
            >
              Start maintenance
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs">
            Every <code className="mono">/v1</code> call will return 503 until you lift this.
            Admin, health and public pages stay reachable.
          </div>
          <Field label="Message to partners" hint={`${startForm.message.length}/300`}>
            <input
              className="input"
              maxLength={300}
              value={startForm.message}
              onChange={(event) => setStartForm({ ...startForm, message: event.target.value })}
            />
          </Field>
          <Field label="Expected end" hint="Optional; used for Retry-After">
            <input
              className="input"
              type="datetime-local"
              value={startForm.expected_end}
              onChange={(event) => setStartForm({ ...startForm, expected_end: event.target.value })}
            />
          </Field>
          <Field label="Reason (internal, required)">
            <input
              className="input"
              value={startForm.reason}
              onChange={(event) => setStartForm({ ...startForm, reason: event.target.value })}
              placeholder="e.g. database migration 0042"
            />
          </Field>
          <Field label={`Type ${CONFIRM_WORD} to confirm`}>
            <input
              className="input mono"
              value={startForm.confirm}
              onChange={(event) => setStartForm({ ...startForm, confirm: event.target.value })}
              placeholder={CONFIRM_WORD}
              autoComplete="off"
            />
          </Field>
        </div>
      </Modal>

      {/* Suspend partner */}
      <Modal
        open={!!suspendTarget}
        onClose={() => setSuspendTarget(null)}
        title={`Suspend ${suspendTarget?.name}?`}
        description="Only this partner's keys stop working."
        footer={
          <>
            <button className="btn-secondary" onClick={() => setSuspendTarget(null)} disabled={busy}>
              Cancel
            </button>
            <button
              className="btn-danger"
              onClick={doSuspend}
              disabled={busy || !suspendForm.internal.trim()}
            >
              Suspend partner
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Internal reason (required)" hint="Never shown to the partner">
            <input
              className="input"
              value={suspendForm.internal}
              onChange={(event) => setSuspendForm({ ...suspendForm, internal: event.target.value })}
            />
          </Field>
          <Field label="Message shown to the partner" hint="Optional">
            <input
              className="input"
              maxLength={300}
              value={suspendForm.public}
              onChange={(event) => setSuspendForm({ ...suspendForm, public: event.target.value })}
              placeholder="Access is temporarily suspended. Contact support."
            />
          </Field>
        </div>
      </Modal>

      {/* Schedule window */}
      <Modal
        open={windowOpen}
        onClose={() => setWindowOpen(false)}
        title="Schedule maintenance"
        description="The public site shows a banner 24 hours before the window."
        footer={
          <>
            <button className="btn-secondary" onClick={() => setWindowOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={createWindow}
              disabled={busy || !windowForm.starts_at || !windowForm.ends_at || !windowForm.banner_text}
            >
              Schedule
            </button>
          </>
        }
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Starts at">
            <input
              className="input"
              type="datetime-local"
              value={windowForm.starts_at}
              onChange={(event) => setWindowForm({ ...windowForm, starts_at: event.target.value })}
            />
          </Field>
          <Field label="Ends at">
            <input
              className="input"
              type="datetime-local"
              value={windowForm.ends_at}
              onChange={(event) => setWindowForm({ ...windowForm, ends_at: event.target.value })}
            />
          </Field>
          <Field label="Banner text" className="sm:col-span-2">
            <input
              className="input"
              maxLength={300}
              value={windowForm.banner_text}
              onChange={(event) =>
                setWindowForm({ ...windowForm, banner_text: event.target.value })
              }
              placeholder="Planned database upgrade"
            />
          </Field>
          <div className="sm:col-span-2">
            <Toggle
              checked={windowForm.auto_enable}
              onChange={(value) => setWindowForm({ ...windowForm, auto_enable: value })}
              label="Enable automatically"
              description="Turn maintenance on at the start time and off at the end time."
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={stopConfirm}
        onClose={() => setStopConfirm(false)}
        onConfirm={stopMaintenance}
        busy={busy}
        title="Lift maintenance?"
        confirmLabel="Lift maintenance"
        danger={false}
        message="Partners will be able to call the API again immediately."
      />
    </div>
  )
}

function Row({ label, children, mono }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-muted shrink-0">{label}</span>
      <span className={`text-right break-all ${mono ? 'mono' : ''}`}>{children}</span>
    </div>
  )
}
