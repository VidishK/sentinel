import { useMemo, useState } from 'react'
import type { PendingPreview, SessionAction } from '../types'

function riskClass(level: string) {
  const l = level.toLowerCase()
  if (l.includes('high')) return 'high'
  if (l.includes('med')) return 'medium'
  return 'low'
}

function rowsJson(rows?: Record<string, unknown>[] | null) {
  if (!rows || rows.length === 0) return '—'
  return JSON.stringify(rows, null, 2)
}

function fromSessionAction(action: SessionAction): PendingPreview {
  const summary = (action.diff_summary ?? {}) as Record<string, unknown>
  const reasons = Array.isArray(summary.reasons)
    ? (summary.reasons as string[])
    : []
  return {
    action_id: action.id,
    status: action.status,
    sql: action.sql_text,
    risk: {
      level: action.risk,
      reasons,
      requires_backup: action.risk === 'high',
    },
    rows_affected: action.rows_affected,
    error: null,
  }
}

interface PreviewPanelProps {
  pending: PendingPreview[]
  actions: SessionAction[]
  busyId: string | null
  confirmLevels: string[]
  confirmPhrase: string
  onApprove: (actionId: string, confirmation?: string) => void
  onReject: (actionId: string) => void
}

export function PreviewPanel({
  pending,
  actions,
  busyId,
  confirmLevels,
  confirmPhrase,
  onApprove,
  onReject,
}: PreviewPanelProps) {
  const [confirmFor, setConfirmFor] = useState<string | null>(null)
  const [typed, setTyped] = useState('')

  const pendingIds = useMemo(
    () => new Set(pending.map((p) => p.action_id)),
    [pending],
  )

  const awaiting = [
    ...pending.filter(
      (p) =>
        p.status === 'awaiting_approval' ||
        p.status === 'previewed' ||
        (!p.error &&
          p.status !== 'failed' &&
          p.status !== 'committed' &&
          p.status !== 'rejected' &&
          p.status !== 'auto_committed'),
    ),
    ...actions
      .filter((a) => a.status === 'previewed' && !pendingIds.has(a.id))
      .map(fromSessionAction),
  ]

  const recent = [...actions]
    .filter((a) => a.status !== 'previewed')
    .reverse()
    .slice(0, 8)

  function needsConfirm(level: string) {
    return confirmLevels.map((x) => x.toLowerCase()).includes(level.toLowerCase())
  }

  function requestApprove(preview: PendingPreview) {
    if (needsConfirm(String(preview.risk.level))) {
      setConfirmFor(preview.action_id)
      setTyped('')
      return
    }
    onApprove(preview.action_id)
  }

  if (awaiting.length === 0 && recent.length === 0) {
    return (
      <p className="muted">
        No changes waiting. Low-risk bounded writes apply on their own
        (see Audit). Medium/high risk still land here for{' '}
        <strong>Approve &amp; commit</strong> or <strong>Reject</strong>.
      </p>
    )
  }

  return (
    <div className="stack">
      {awaiting.map((preview) => (
        <section key={preview.action_id} className="block">
          <div className="block-title">
            <h3>Awaiting approval</h3>
            <span className={`risk ${riskClass(String(preview.risk.level))}`}>
              {preview.risk.level}
            </span>
          </div>
          <pre className="sql-block">{preview.sql}</pre>
          {preview.rationale ? (
            <p className="muted" style={{ marginTop: 8 }}>
              {preview.rationale}
            </p>
          ) : null}
          <ul className="reason-list">
            <li>
              Rows affected:{' '}
              {preview.rows_affected == null ? 'n/a' : preview.rows_affected}
            </li>
            {(preview.risk.reasons ?? []).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
            {needsConfirm(String(preview.risk.level)) ? (
              <li>
                Typed confirmation required ({confirmPhrase})
              </li>
            ) : null}
            {preview.risk.requires_backup ||
            String(preview.risk.level).toLowerCase() === 'high' ? (
              <li>
                High risk: latest ccloud backup + cluster timestamp will be
                recorded before commit
              </li>
            ) : null}
          </ul>
          <div className="diff-grid">
            <div className="diff-col">
              <h4>Before</h4>
              <pre>{rowsJson(preview.before_sample ?? preview.before)}</pre>
            </div>
            <div className="diff-col">
              <h4>After</h4>
              <pre>{rowsJson(preview.after_sample ?? preview.after)}</pre>
            </div>
          </div>

          {confirmFor === preview.action_id ? (
            <div className="confirm-box">
              <p>
                This is <strong>{preview.risk.level}</strong> risk. Type{' '}
                <code className="mono">{confirmPhrase}</code> to commit.
              </p>
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={confirmPhrase}
                aria-label="Confirmation phrase"
              />
              <div className="action-row">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={
                    busyId === preview.action_id || typed.trim() !== confirmPhrase
                  }
                  onClick={() => {
                    onApprove(preview.action_id, typed.trim())
                    setConfirmFor(null)
                    setTyped('')
                  }}
                >
                  Confirm commit
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => {
                    setConfirmFor(null)
                    setTyped('')
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="action-row">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busyId === preview.action_id || Boolean(preview.error)}
                onClick={() => requestApprove(preview)}
              >
                {busyId === preview.action_id ? 'Committing…' : 'Approve & commit'}
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busyId === preview.action_id}
                onClick={() => onReject(preview.action_id)}
              >
                Reject
              </button>
              <span className="meta muted mono" style={{ alignSelf: 'center' }}>
                {preview.action_id.slice(0, 8)}
              </span>
            </div>
          )}
        </section>
      ))}

      {recent.length > 0 ? (
        <section className="block">
          <div className="block-title">
            <h3>Already handled this session</h3>
          </div>
          <div className="stack">
            {recent.map((action) => (
              <div key={action.id} className="audit-item">
                <div className="top">
                  <span className={`risk ${riskClass(String(action.risk))}`}>
                    {action.risk} · {action.status}
                  </span>
                  <span className="when">
                    {new Date(action.created_at).toLocaleTimeString()}
                  </span>
                </div>
                <pre className="sql-block">{action.sql_text}</pre>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}
