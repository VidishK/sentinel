import type { SessionAction } from '../types'

interface AuditPanelProps {
  actions: SessionAction[]
}

function riskClass(level: string) {
  const l = level.toLowerCase()
  if (l.includes('high')) return 'high'
  if (l.includes('med')) return 'medium'
  return 'low'
}

export function AuditPanel({ actions }: AuditPanelProps) {
  if (actions.length === 0) {
    return (
      <p className="muted">
        Audit events for this session will appear as Sentinel rehearses and
        commits changes.
      </p>
    )
  }

  const ordered = [...actions].sort(
    (a, b) => +new Date(b.created_at) - +new Date(a.created_at),
  )

  return (
    <div className="stack">
      {ordered.map((action) => (
        <section key={action.id} className="block audit-item">
          <div className="top">
            <span className={`risk ${riskClass(String(action.risk))}`}>
              {action.kind} · {action.status}
            </span>
            <span className="when">
              {new Date(action.created_at).toLocaleString()}
            </span>
          </div>
          <pre className="sql-block">{action.sql_text}</pre>
          <div className="muted" style={{ fontSize: '0.82rem' }}>
            action {action.id}
            {action.rows_affected != null
              ? ` · rows ${action.rows_affected}`
              : ''}
            {action.committed_at
              ? ` · committed ${new Date(action.committed_at).toLocaleString()}`
              : ''}
            {action.backup_id ? ` · restore ${action.backup_id}` : ''}
          </div>
        </section>
      ))}
    </div>
  )
}
