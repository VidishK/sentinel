import { useEffect, useState } from 'react'
import {
  activateConnection,
  createConnection,
  deleteConnection,
  listConnections,
  refreshConnections,
} from '../api'
import type { DataConnection } from '../types'

export function SourcesPanel() {
  const [rows, setRows] = useState<DataConnection[]>([])
  const [name, setName] = useState('')
  const [engine, setEngine] = useState('postgres')
  const [dsn, setDsn] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function reload() {
    const data = await listConnections()
    setRows(data.connections)
  }

  useEffect(() => {
    reload().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : 'Failed to load sources')
    })
  }, [])

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await createConnection(name.trim(), engine, dsn.trim())
      setName('')
      setDsn('')
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <section className="block">
        <div className="block-title">
          <h3>Connect a database</h3>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Sentinel memory stays on Cockroach, private to your account.
          Connect Postgres, Cockroach, or Mongo that this host can reach.
          Only you can see that source. The shared demo is a playground —
          your company database is not.
        </p>
        <form className="rule-form" onSubmit={(e) => void handleAdd(e)}>
          <label>
            Name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Production replica"
              required
            />
          </label>
          <label>
            Engine
            <select
              value={engine}
              onChange={(e) => setEngine(e.target.value)}
            >
              <option value="postgres">PostgreSQL</option>
              <option value="cockroach">CockroachDB</option>
              <option value="mongodb">MongoDB</option>
            </select>
          </label>
          <label>
            Connection string
            <input
              value={dsn}
              onChange={(e) => setDsn(e.target.value)}
              placeholder="postgresql://user:pass@host:5432/db or mongodb://..."
              required
            />
          </label>
          <div className="action-row">
            <button
              type="submit"
              className="btn btn-secondary"
              disabled={busy || !name.trim() || !dsn.trim()}
            >
              {busy ? 'Connecting…' : 'Connect & introspect'}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy}
              onClick={() =>
                void refreshConnections()
                  .then(reload)
                  .catch((err: unknown) =>
                    setError(err instanceof Error ? err.message : 'Refresh failed'),
                  )
              }
            >
              Refresh catalog
            </button>
          </div>
        </form>
        {error ? <p className="error-banner">{error}</p> : null}
      </section>

      {rows.map((row) => (
        <section key={row.id} className="block">
          <div className="block-title">
            <h3>{row.name}</h3>
            <span className={row.active ? 'rule-status trusted' : 'muted'}>
              {row.active ? 'active' : row.engine}
            </span>
          </div>
          <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
            {row.engine} · {row.object_count} objects
          </p>
          <p className="mono muted" style={{ fontSize: '0.75rem', margin: '6px 0 0' }}>
            {row.dsn_masked}
          </p>
          <div className="action-row">
            {!row.active ? (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void activateConnection(row.id)
                    .then(reload)
                    .catch((err: unknown) =>
                      setError(err instanceof Error ? err.message : 'Activate failed'),
                    )
                }
              >
                Make active
              </button>
            ) : null}
            {!row.active ? (
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() =>
                  void deleteConnection(row.id)
                    .then(reload)
                    .catch((err: unknown) =>
                      setError(err instanceof Error ? err.message : 'Delete failed'),
                    )
                }
              >
                Remove
              </button>
            ) : null}
          </div>
        </section>
      ))}
    </div>
  )
}
