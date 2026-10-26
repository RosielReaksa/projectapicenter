import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { datetime, relative } from '../lib/format'
import { useToast } from '../lib/toast'
import { useAuth } from '../lib/auth'
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

export default function SettingsPage() {
  const toast = useToast()
  const { refresh } = useAuth()
  const [settings, setSettings] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [audit, setAudit] = useState([])
  const [confirm, setConfirm] = useState(null)
  const [totp, setTotp] = useState(null)
  const [totpCode, setTotpCode] = useState('')
  const [password, setPassword] = useState({ current: '', next: '' })
  const [members, setMembers] = useState([])

  const load = useCallback(async () => {
    try {
      const [config, log, memberData] = await Promise.all([
        api.get('/admin/settings'),
        api.get('/admin/audit'),
        api.get('/admin/members'),
      ])
      setSettings(config)
      setAudit(log.entries)
      setMembers(memberData.members)
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const save = async (patch) => {
    setSaving(true)
    try {
      await api.put('/admin/settings', patch)
      toast.success('Settings saved')
      await load()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const startTotp = async () => {
    try {
      setTotp(await api.post('/admin/auth/totp/setup'))
    } catch (err) {
      toast.error(err.message)
    }
  }

  const enableTotp = async () => {
    try {
      await api.post('/admin/auth/totp/enable', { code: totpCode })
      toast.success('Two-factor enabled')
      setTotp(null)
      setTotpCode('')
      await load()
      await refresh()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const changePassword = async () => {
    try {
      await api.post('/admin/auth/password', {
        current_password: password.current,
        new_password: password.next,
      })
      toast.success('Password changed')
      setPassword({ current: '', next: '' })
    } catch (err) {
      toast.error(err.message)
    }
  }

  const dangerAction = async (action) => {
    try {
      const result = await api.post(`/admin/danger/${action}`)
      toast.success(
        action === 'purge-logs'
          ? `Deleted ${result.deleted} old request rows`
          : 'Done',
      )
      setConfirm(null)
      load()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const exportData = async () => {
    try {
      const data = await api.get('/admin/export')
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'gateway-export.json'
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err.message)
    }
  }

  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!settings) return <Spinner label="Loading settings" />

  return (
    <div className="space-y-5">
      <header>
        <h1 className="page-title">Settings</h1>
        <p className="text-xs text-muted mt-1">Security, limits, logging and retention.</p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Spend and limits">
          <div className="space-y-4">
            <Field
              label="Global monthly spend cap (USD)"
              hint="Past this amount, only free-tier models may run."
            >
              <div className="flex gap-2">
                <input
                  className="input mono"
                  type="number"
                  min="0"
                  step="0.01"
                  defaultValue={settings.global_monthly_cap_usd}
                  id="cap-input"
                />
                <button
                  className="btn-secondary"
                  onClick={() =>
                    save({ global_monthly_cap_usd: Number(document.getElementById('cap-input').value) })
                  }
                  disabled={saving}
                >
                  Save
                </button>
              </div>
            </Field>

            <Field label="Max attempts per request" hint="Provider fallbacks">
              <input
                className="input mono"
                type="number"
                min="1"
                max="10"
                defaultValue={settings.max_attempts}
                id="attempts-input"
              />
            </Field>
            <button
              className="btn-secondary btn-sm"
              onClick={() =>
                save({ max_attempts: Number(document.getElementById('attempts-input').value) })
              }
              disabled={saving}
            >
              Save attempts
            </button>

            <Field label="Request timeout (seconds)">
              <input
                className="input mono"
                type="number"
                min="1"
                max="600"
                defaultValue={settings.request_timeout_seconds}
                id="timeout-input"
              />
            </Field>
            <button
              className="btn-secondary btn-sm"
              onClick={() =>
                save({ request_timeout_seconds: Number(document.getElementById('timeout-input').value) })
              }
              disabled={saving}
            >
              Save timeout
            </button>
          </div>
        </Card>

        <Card title="Privacy and retention">
          <div className="space-y-4">
            <Field
              label="Prompt logging"
              hint="Metadata only stores message counts and lengths, never text."
            >
              <select
                className="select"
                value={settings.log_prompts}
                onChange={(event) => save({ log_prompts: event.target.value })}
              >
                <option value="off">Off — nothing stored</option>
                <option value="metadata">Metadata only (recommended)</option>
                <option value="full">Full — stores request and response text</option>
              </select>
            </Field>

            {settings.log_prompts === 'full' && (
              <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs">
                Full logging stores partner and end-user content in the database. Confirm each
                partner has agreed to this.
              </div>
            )}

            <Field label="Data retention (days)">
              <div className="flex gap-2">
                <input
                  className="input mono"
                  type="number"
                  min="1"
                  max="3650"
                  defaultValue={settings.retention_days}
                  id="retention-input"
                />
                <button
                  className="btn-secondary"
                  onClick={() =>
                    save({ retention_days: Number(document.getElementById('retention-input').value) })
                  }
                  disabled={saving}
                >
                  Save
                </button>
              </div>
            </Field>

            <Toggle
              checked={settings.cache_enabled}
              onChange={(value) => save({ cache_enabled: value })}
              label="Response cache"
              description="Applies only to temperature-0 requests, scoped per partner."
            />

            <Field label="Cache TTL (seconds)">
              <input
                className="input mono"
                type="number"
                min="0"
                defaultValue={settings.cache_ttl_seconds}
                id="ttl-input"
              />
            </Field>
            <button
              className="btn-secondary btn-sm"
              onClick={() =>
                save({ cache_ttl_seconds: Number(document.getElementById('ttl-input').value) })
              }
              disabled={saving}
            >
              Save TTL
            </button>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Your account" subtitle="Password and two-factor authentication">
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2.5">
              <div>
                <div className="text-sm">Two-factor authentication</div>
                <div className="text-xs text-muted">
                  {settings.log_prompts !== undefined
                    ? 'Strongly recommended for the owner account.'
                    : ''}
                </div>
              </div>
              {totp ? (
                <Badge tone="warning">Setup</Badge>
              ) : (
                <Badge tone="neutral">Use the button below</Badge>
              )}
            </div>

            {!totp ? (
              <button className="btn-secondary w-full" onClick={startTotp}>
                Set up two-factor
              </button>
            ) : (
              <div className="space-y-3 rounded-md border border-border p-3">
                {totp.qr_png_base64 ? (
                  <img
                    src={`data:image/png;base64,${totp.qr_png_base64}`}
                    alt="Two-factor QR code"
                    className="mx-auto h-40 w-40 rounded bg-white p-2"
                  />
                ) : (
                  <p className="text-xs break-all mono">{totp.otpauth_uri}</p>
                )}
                <p className="text-xs text-muted">
                  Scan this with your authenticator app, then enter the 6-digit code.
                </p>
                <div className="flex gap-2">
                  <input
                    className="input mono text-center tracking-[0.3em]"
                    maxLength={6}
                    placeholder="000000"
                    value={totpCode}
                    onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, ''))}
                  />
                  <button className="btn-primary" onClick={enableTotp} disabled={totpCode.length !== 6}>
                    Enable
                  </button>
                </div>
              </div>
            )}

            <div className="pt-3 border-t border-border space-y-2">
              <Field label="Current password">
                <input
                  className="input"
                  type="password"
                  value={password.current}
                  onChange={(event) => setPassword({ ...password, current: event.target.value })}
                />
              </Field>
              <Field label="New password" hint="At least 10 characters">
                <input
                  className="input"
                  type="password"
                  value={password.next}
                  onChange={(event) => setPassword({ ...password, next: event.target.value })}
                />
              </Field>
              <button
                className="btn-secondary w-full"
                onClick={changePassword}
                disabled={!password.current || password.next.length < 10}
              >
                Change password
              </button>
            </div>
          </div>
        </Card>

        <Card title="Console access">
          <dl className="space-y-2 text-xs">
            <div className="flex justify-between">
              <dt className="text-muted">Environment</dt>
              <dd className="capitalize">{settings.environment}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Database</dt>
              <dd className="mono">{settings.database}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Shared state</dt>
              <dd className="mono">{settings.shared_state}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Secure cookies</dt>
              <dd>{settings.cookie_secure ? 'yes' : 'no — enable behind HTTPS'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Concurrency slot TTL</dt>
              <dd className="mono">{settings.concurrency_slot_ttl}s</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Idempotency TTL</dt>
              <dd className="mono">{settings.idempotency_ttl_seconds}s</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Cache entries</dt>
              <dd className="mono">{settings.cache.entries}</dd>
            </div>
          </dl>

          <Field
            label="Console IP allow list"
            hint="Comma separated. Blank = any IP. Also settable in .env."
            className="mt-4"
          >
            <div className="flex gap-2">
              <input
                className="input mono text-xs"
                defaultValue={settings.admin_allowed_ips}
                id="ip-input"
              />
              <button
                className="btn-secondary"
                onClick={() => save({ admin_allowed_ips: document.getElementById('ip-input').value })}
                disabled={saving}
              >
                Save
              </button>
            </div>
          </Field>
        </Card>
      </div>

      <Card title="Console members" subtitle="Owner and partner logins" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Email</th>
              <th className="th">Role</th>
              <th className="th">Partner</th>
              <th className="th">2FA</th>
              <th className="th">Status</th>
              <th className="th">Created</th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr key={member.id}>
                <td className="td text-xs">{member.email}</td>
                <td className="td text-xs capitalize">{member.role}</td>
                <td className="td text-xs mono">{member.partner_id ?? '—'}</td>
                <td className="td">
                  <Badge tone={member.totp_enabled ? 'success' : 'neutral'}>
                    {member.totp_enabled ? 'on' : 'off'}
                  </Badge>
                </td>
                <td className="td">
                  <Badge tone={member.is_active ? 'success' : 'neutral'}>
                    {member.is_active ? 'active' : 'disabled'}
                  </Badge>
                </td>
                <td className="td text-xs text-muted">{datetime(member.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Audit log" subtitle="Append-only record of console actions" bodyClass="p-0">
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Time</th>
                <th className="th">Action</th>
                <th className="th">Target</th>
                <th className="th">IP</th>
              </tr>
            </thead>
            <tbody>
              {audit.length === 0 ? (
                <tr>
                  <td className="td text-xs text-muted" colSpan={4}>
                    No entries yet.
                  </td>
                </tr>
              ) : (
                audit.map((entry) => (
                  <tr key={entry.id}>
                    <td className="td text-xs text-muted whitespace-nowrap" title={entry.time}>
                      {relative(entry.time)}
                    </td>
                    <td className="td mono text-xs">{entry.action}</td>
                    <td className="td text-xs truncate max-w-[220px]">{entry.target || '—'}</td>
                    <td className="td mono text-xs text-muted">{entry.ip || '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Danger zone" className="border-danger/30">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm">Export configuration</div>
              <div className="text-xs text-muted">Partners, providers, models, keys. No secrets.</div>
            </div>
            <button className="btn-secondary btn-sm" onClick={exportData}>
              Download JSON
            </button>
          </div>

          <div className="flex items-center justify-between gap-4 border-t border-border pt-3">
            <div>
              <div className="text-sm">Clear response cache</div>
              <div className="text-xs text-muted">All cached answers are discarded.</div>
            </div>
            <button
              className="btn-secondary btn-sm"
              onClick={() => setConfirm({ action: 'clear-cache', title: 'Clear the cache?' })}
            >
              Clear cache
            </button>
          </div>

          <div className="flex items-center justify-between gap-4 border-t border-border pt-3">
            <div>
              <div className="text-sm">Reset circuit breakers</div>
              <div className="text-xs text-muted">Re-enables every provider immediately.</div>
            </div>
            <button
              className="btn-secondary btn-sm"
              onClick={() => setConfirm({ action: 'reset-health', title: 'Reset all breakers?' })}
            >
              Reset health
            </button>
          </div>

          <div className="flex items-center justify-between gap-4 border-t border-border pt-3">
            <div>
              <div className="text-sm text-danger">Purge old request logs</div>
              <div className="text-xs text-muted">
                Deletes request rows older than the retention window.
              </div>
            </div>
            <button
              className="btn-danger btn-sm"
              onClick={() => setConfirm({ action: 'purge-logs', title: 'Purge old logs?' })}
            >
              Purge logs
            </button>
          </div>
        </div>
      </Card>

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={() => dangerAction(confirm.action)}
        title={confirm?.title}
        confirmLabel="Confirm"
        message="This action takes effect immediately and cannot be undone."
      />
    </div>
  )
}
