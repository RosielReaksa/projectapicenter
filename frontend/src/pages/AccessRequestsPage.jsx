import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { datetime, relative } from '../lib/format'
import { useToast } from '../lib/toast'
import {
  Badge,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorBox,
  Modal,
  Spinner,
} from '../components/ui'

const FILTERS = [
  ['pending', 'Pending'],
  ['approved', 'Approved'],
  ['rejected', 'Rejected'],
  ['', 'All'],
]

export default function AccessRequestsPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('pending')
  const [detail, setDetail] = useState(null)
  const [rejecting, setRejecting] = useState(null)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.get(`/admin/access-requests${filter ? `?status=${filter}` : ''}`))
      setError(null)
    } catch (err) {
      setError(err)
    }
  }, [filter])

  useEffect(() => {
    load()
  }, [load])

  const approve = async (row) => {
    setBusy(true)
    try {
      const result = await api.post(`/admin/access-requests/${row.id}/approve`)
      toast.success('Approved — opening the partner form')
      setDetail(null)
      // Approving hands the owner a prefilled partner form (§4.4).
      navigate('/partners', { state: { prefill: result.prefill } })
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    setBusy(true)
    try {
      await api.post(`/admin/access-requests/${rejecting.id}/reject`, { notes: notes || null })
      toast.success('Request rejected')
      setRejecting(null)
      setNotes('')
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
          <h1 className="page-title">Access Requests</h1>
          <p className="text-xs text-muted mt-1">
            Public requests for an invitation. Approving opens the partner form — it never creates
            an account or key automatically.
          </p>
        </div>
        <div className="flex rounded-md border border-border p-0.5">
          {FILTERS.map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`px-2.5 h-7 rounded text-xs transition-colors ${
                filter === value ? 'bg-accent-soft text-accent font-medium' : 'text-muted hover:text-fg'
              }`}
            >
              {label}
              {value === 'pending' && data?.pending_count > 0 && (
                <span className="ml-1.5 mono text-accent">{data.pending_count}</span>
              )}
            </button>
          ))}
        </div>
      </header>

      <div className="card overflow-hidden">
        {!data ? (
          <Spinner label="Loading requests" />
        ) : data.requests.length === 0 ? (
          <EmptyState
            title={filter === 'pending' ? 'No pending requests' : 'Nothing here'}
            description="Requests submitted through the public form appear here."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Company</th>
                  <th className="th">Expected usage</th>
                  <th className="th">Received</th>
                  <th className="th">Status</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {data.requests.map((row) => (
                  <tr key={row.id} className="row-hover" onClick={() => setDetail(row)}>
                    <td className="td">
                      <div className="font-medium">{row.name}</div>
                      <div className="text-xs text-muted">{row.email}</div>
                    </td>
                    <td className="td text-xs truncate max-w-[180px]">{row.company}</td>
                    <td className="td text-xs">{row.expected_usage}</td>
                    <td className="td text-xs text-muted whitespace-nowrap" title={row.created_at}>
                      {relative(row.created_at)}
                    </td>
                    <td className="td">
                      <Badge
                        tone={
                          row.status === 'approved'
                            ? 'success'
                            : row.status === 'rejected'
                              ? 'danger'
                              : 'warning'
                        }
                      >
                        {row.status}
                      </Badge>
                    </td>
                    <td className="td">
                      {row.status === 'pending' && (
                        <div className="flex justify-end gap-1">
                          <button
                            className="btn-ghost btn-sm"
                            onClick={(event) => {
                              event.stopPropagation()
                              setDetail(row)
                            }}
                          >
                            Review
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail?.name || 'Request'}
        description={detail?.email}
        width="max-w-xl"
        footer={
          detail?.status === 'pending' ? (
            <>
              <button className="btn-secondary" onClick={() => setDetail(null)} disabled={busy}>
                Close
              </button>
              <button
                className="btn-danger"
                onClick={() => {
                  setRejecting(detail)
                  setDetail(null)
                }}
                disabled={busy}
              >
                Reject
              </button>
              <button className="btn-primary" onClick={() => approve(detail)} disabled={busy}>
                {busy ? 'Working…' : 'Approve'}
              </button>
            </>
          ) : (
            <button className="btn-secondary" onClick={() => setDetail(null)}>
              Close
            </button>
          )
        }
      >
        {detail && (
          <div className="space-y-4">
            <Row label="Company">{detail.company}</Row>
            <Row label="Expected usage">{detail.expected_usage}</Row>
            <Row label="Received">{datetime(detail.created_at)}</Row>
            <Row label="Status">
              <Badge tone={detail.status === 'pending' ? 'warning' : detail.status === 'approved' ? 'success' : 'danger'}>
                {detail.status}
              </Badge>
            </Row>
            {detail.ip && <Row label="Source IP" mono>{detail.ip}</Row>}

            <div>
              <div className="text-xs text-muted mb-1">What will they use it for?</div>
              {/* Rendered as text, never as HTML: user-supplied content (§10.9). */}
              <p className="whitespace-pre-wrap text-sm rounded-md border border-border bg-surface p-3">
                {detail.use_case}
              </p>
            </div>

            {detail.notes && (
              <div>
                <div className="text-xs text-muted mb-1">Review notes</div>
                <p className="whitespace-pre-wrap text-sm">{detail.notes}</p>
              </div>
            )}

            {detail.status === 'pending' && (
              <p className="text-xs text-muted">
                Approving opens the partner form prefilled with this company and email. You choose
                the limits and create the first key yourself.
              </p>
            )}
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!rejecting}
        onClose={() => setRejecting(null)}
        onConfirm={reject}
        busy={busy}
        title="Reject this request?"
        confirmLabel="Reject"
        message={`${rejecting?.email} will be marked rejected. No account is created.`}
      />
    </div>
  )
}

function Row({ label, children, mono }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-xs text-muted shrink-0">{label}</span>
      <span className={`text-xs text-right break-all ${mono ? 'mono' : ''}`}>{children}</span>
    </div>
  )
}
