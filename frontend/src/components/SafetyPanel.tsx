import { useEffect, useState } from 'react'
import { listBackups, reindexSchemaMemory } from '../api'
import type { Policy } from '../types'

interface SafetyPanelProps {
  policy: Policy | null
  saving: boolean
  onUpdate: (patch: Partial<Policy>) => Promise<void>
}

export function SafetyPanel({ policy, saving, onUpdate }: SafetyPanelProps) {
  const [reindexing, setReindexing] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [backups, setBackups] = useState<{ id: string; as_of_time?: string }[]>(
    [],
  )
  const [backupError, setBackupError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listBackups()
      .then((data) => {
        if (!cancelled) setBackups(data.backups.slice(-4).reverse())
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setBackupError(err instanceof Error ? err.message : 'Backup list failed')
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!policy) {
    return <p className="muted">Loading guardrails…</p>
  }

  const readonly = policy.mode === 'readonly' || !policy.allow_writes

  async function handleReindex() {
    setReindexing(true)
    setNote(null)
    try {
      const result = await reindexSchemaMemory()
      setNote(`Indexed ${result.upserted} schema docs with Titan embeddings.`)
    } catch (err) {
      setNote(err instanceof Error ? err.message : 'Reindex failed')
    } finally {
      setReindexing(false)
    }
  }
  return (
    <div className="stack">
      <section className="block">
        <div className="block-title">
          <h3>Access posture</h3>
          <span>{readonly ? 'locked down' : 'strict writes'}</span>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Sentinel is not open database access. Basic low-risk writes on the
          active source can apply automatically. Medium and high risk still
          rehearse first and wait in Preview.
        </p>
        <div className="action-row">
          <button
            type="button"
            className={`btn ${readonly ? 'btn-primary' : 'btn-secondary'}`}
            disabled={saving || readonly}
            onClick={() => void onUpdate({ mode: 'readonly', allow_writes: false })}
          >
            Read-only
          </button>
          <button
            type="button"
            className={`btn ${!readonly ? 'btn-primary' : 'btn-secondary'}`}
            disabled={saving || !readonly}
            onClick={() => void onUpdate({ mode: 'strict', allow_writes: true })}
          >
            Strict writes
          </button>
        </div>
      </section>

      <section className="block">
        <div className="block-title">
          <h3>Hard gates</h3>
        </div>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={policy.block_ddl}
            disabled={saving}
            onChange={(e) => void onUpdate({ block_ddl: e.target.checked })}
          />
          <span>Block DDL (CREATE / ALTER / DROP / TRUNCATE)</span>
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={policy.require_where}
            disabled={saving}
            onChange={(e) => void onUpdate({ require_where: e.target.checked })}
          />
          <span>Require WHERE on UPDATE / DELETE</span>
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={policy.auto_apply_low_risk !== false}
            disabled={saving}
            onChange={(e) =>
              void onUpdate({ auto_apply_low_risk: e.target.checked })
            }
          />
          <span>Auto-apply basic (low-risk, bounded) writes</span>
        </label>
        <label className="toggle-row">
          Auto-apply max rows
          <input
            type="number"
            min={1}
            max={100}
            value={policy.auto_apply_max_rows ?? 25}
            disabled={saving || policy.auto_apply_low_risk === false}
            onChange={(e) =>
              void onUpdate({
                auto_apply_max_rows: Number(e.target.value) || 25,
              })
            }
          />
        </label>
        <label className="toggle-row">
          Max rows per preview
          <input
            type="number"
            min={1}
            max={50000}
            value={policy.max_preview_rows}
            disabled={saving}
            onChange={(e) =>
              void onUpdate({ max_preview_rows: Number(e.target.value) || 500 })
            }
          />
        </label>
      </section>

      <section className="block">
        <div className="block-title">
          <h3>Active policy</h3>
        </div>
        <ul className="reason-list">
          {policy.summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="muted" style={{ marginTop: 10, fontSize: '0.85rem' }}>
          Medium/high risk commits require typing{' '}
          <code className="mono">{policy.confirm_phrase}</code> in Preview.
        </p>
      </section>

      <section className="block">
        <div className="block-title">
          <h3>Vector memory</h3>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Schema docs, rules, and action intents are embedded with Titan and
          searched with CockroachDB VECTOR indexes on every chat turn.
        </p>
        <div className="action-row">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={saving || reindexing}
            onClick={() => void handleReindex()}
          >
            {reindexing ? 'Re-indexing…' : 'Re-index schema embeddings'}
          </button>
        </div>
        {note ? <p className="muted">{note}</p> : null}
      </section>

      <section className="block">
        <div className="block-title">
          <h3>ccloud undo points</h3>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          High-risk commits record the latest Cockroach Cloud automatic backup
          plus a cluster_logical_timestamp() so the write can be inspected
          later with AS OF SYSTEM TIME.
        </p>
        {backupError ? (
          <p className="error-banner">{backupError}</p>
        ) : backups.length === 0 ? (
          <p className="muted">No backups listed yet.</p>
        ) : (
          <ul className="reason-list">
            {backups.map((row) => (
              <li key={row.id}>
                {row.id.slice(0, 8)}… · {row.as_of_time ?? 'unknown time'}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
