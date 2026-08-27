import { useMemo, useState } from 'react'
import type { Rule, RuleSearchHit } from '../types'

interface RulesPanelProps {
  rules: Rule[]
  busy: boolean
  evaluatingId: string | null
  actingId: string | null
  searchHits: RuleSearchHit[]
  searching: boolean
  onCreate: (body: string, triggerText: string) => Promise<void>
  onEvaluate: (ruleId: string) => Promise<void>
  onSearch: (query: string, status?: string) => Promise<void>
  onClearSearch: () => void
  onUpdate: (ruleId: string, body: string, trigger: string) => Promise<void>
  onStatus: (ruleId: string, status: string) => Promise<void>
  onReembed: (ruleId: string) => Promise<void>
  onFindSimilar: (ruleId: string) => Promise<void>
  similarFor: string | null
  similarHits: RuleSearchHit[]
}

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'probation', label: 'On trial' },
  { id: 'trusted', label: 'Trusted' },
  { id: 'rejected', label: 'Rejected' },
  { id: 'retired', label: 'Retired' },
] as const

function statusLabel(status: string) {
  if (status === 'probation') return 'On trial'
  if (status === 'trusted') return 'Trusted'
  if (status === 'rejected') return 'Rejected'
  if (status === 'retired') return 'Retired'
  return status
}

function statusHint(status: string) {
  if (status === 'probation') return 'Not followed yet. Test it before it becomes trusted.'
  if (status === 'trusted') return 'Sentinel follows this instruction.'
  if (status === 'rejected') return 'Will not be used.'
  if (status === 'retired') return 'Kept for history, not used.'
  return ''
}

export function RulesPanel({
  rules,
  busy,
  evaluatingId,
  actingId,
  searchHits,
  searching,
  onCreate,
  onEvaluate,
  onSearch,
  onClearSearch,
  onUpdate,
  onStatus,
  onReembed,
  onFindSimilar,
  similarFor,
  similarHits,
}: RulesPanelProps) {
  const [body, setBody] = useState('')
  const [trigger, setTrigger] = useState('')
  const [filter, setFilter] = useState<(typeof FILTERS)[number]['id']>('all')
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editBody, setEditBody] = useState('')
  const [editTrigger, setEditTrigger] = useState('')
  const [openMenu, setOpenMenu] = useState<string | null>(null)

  const visible = useMemo(() => {
    if (searchHits.length > 0) return null
    if (filter === 'all') return rules
    return rules.filter((r) => r.status === filter)
  }, [rules, filter, searchHits])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!body.trim() || !trigger.trim()) return
    try {
      await onCreate(body.trim(), trigger.trim())
      setBody('')
      setTrigger('')
    } catch {
      // Parent surfaces the error banner.
    }
  }

  function startEdit(rule: Rule) {
    setEditingId(rule.id)
    setEditBody(rule.body)
    setEditTrigger(rule.trigger_text)
    setOpenMenu(null)
  }

  const list = searchHits.length > 0 ? searchHits : (visible ?? [])

  return (
    <div className="stack rules-plain">
      <section className="block">
        <div className="block-title">
          <h3>Instructions</h3>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Tell Sentinel how to behave. New instructions start <strong>on trial</strong>.
          After they prove useful, they become <strong>trusted</strong> and are followed
          automatically.
        </p>
        <form
          className="rule-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (query.trim()) void onSearch(query.trim(), filter === 'all' ? undefined : filter)
          }}
        >
          <label>
            Find an instruction
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. ignore test accounts"
            />
          </label>
          <div className="action-row">
            <button
              type="submit"
              className="btn btn-secondary"
              disabled={searching || !query.trim()}
            >
              {searching ? 'Searching…' : 'Search'}
            </button>
            {searchHits.length > 0 ? (
              <button type="button" className="btn btn-ghost" onClick={onClearSearch}>
                Show all
              </button>
            ) : null}
          </div>
        </form>
      </section>

      <form className="rule-form block" onSubmit={handleSubmit}>
        <div className="block-title">
          <h3>Add an instruction</h3>
        </div>
        <label>
          What should it do?
          <textarea
            rows={3}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Always exclude customers marked as test accounts."
            required
          />
        </label>
        <label>
          When should it apply?
          <input
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
            placeholder="When counting customers or reporting metrics"
            required
          />
        </label>
        <div className="action-row">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || !body.trim() || !trigger.trim()}
          >
            {busy ? 'Saving…' : 'Save for trial'}
          </button>
        </div>
      </form>

      <div className="filter-row">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`chip ${filter === item.id ? 'active' : ''}`}
            onClick={() => setFilter(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {searchHits.length > 0 ? (
        <p className="muted">Closest matches for “{query}”.</p>
      ) : null}

      {list.length === 0 ? (
        <p className="muted">No instructions here yet.</p>
      ) : (
        list.map((rule) => (
          <RuleCard
            key={rule.id}
            rule={rule}
            dist={'dist' in rule && typeof rule.dist === 'number' ? rule.dist : undefined}
            evaluatingId={evaluatingId}
            actingId={actingId}
            editingId={editingId}
            editBody={editBody}
            editTrigger={editTrigger}
            menuOpen={openMenu === rule.id}
            setEditBody={setEditBody}
            setEditTrigger={setEditTrigger}
            onToggleMenu={() =>
              setOpenMenu((current) => (current === rule.id ? null : rule.id))
            }
            onStartEdit={() => startEdit(rule)}
            onCancelEdit={() => setEditingId(null)}
            onSaveEdit={async () => {
              await onUpdate(rule.id, editBody, editTrigger)
              setEditingId(null)
            }}
            onEvaluate={onEvaluate}
            onStatus={onStatus}
            onReembed={onReembed}
            onFindSimilar={onFindSimilar}
            similarHits={similarFor === rule.id ? similarHits : []}
          />
        ))
      )}
    </div>
  )
}

interface RuleCardProps {
  rule: Rule | RuleSearchHit
  dist?: number
  evaluatingId: string | null
  actingId: string | null
  editingId: string | null
  editBody: string
  editTrigger: string
  menuOpen: boolean
  setEditBody: (v: string) => void
  setEditTrigger: (v: string) => void
  onToggleMenu: () => void
  onStartEdit: () => void
  onCancelEdit: () => void
  onSaveEdit: () => Promise<void>
  onEvaluate: (ruleId: string) => Promise<void>
  onStatus: (ruleId: string, status: string) => Promise<void>
  onReembed: (ruleId: string) => Promise<void>
  onFindSimilar: (ruleId: string) => Promise<void>
  similarHits: RuleSearchHit[]
}

function RuleCard({
  rule,
  dist,
  evaluatingId,
  actingId,
  editingId,
  editBody,
  editTrigger,
  menuOpen,
  setEditBody,
  setEditTrigger,
  onToggleMenu,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onEvaluate,
  onStatus,
  onReembed,
  onFindSimilar,
  similarHits,
}: RuleCardProps) {
  const busy = actingId === rule.id || evaluatingId === rule.id
  const editing = editingId === rule.id

  return (
    <section className="block rule-item">
      <div className="block-title">
        <h3>{rule.body}</h3>
        <span className={`rule-status ${rule.status}`}>{statusLabel(rule.status)}</span>
      </div>
      <p className="muted" style={{ margin: 0, fontSize: '0.88rem' }}>
        Applies when: {rule.trigger_text}
        {dist != null ? ` · close match` : ''}
      </p>
      <p className="muted" style={{ margin: '6px 0 0', fontSize: '0.8rem' }}>
        {statusHint(rule.status)}
      </p>

      {editing ? (
        <div className="rule-form" style={{ marginTop: 10 }}>
          <textarea
            rows={3}
            value={editBody}
            onChange={(e) => setEditBody(e.target.value)}
          />
          <input
            value={editTrigger}
            onChange={(e) => setEditTrigger(e.target.value)}
          />
          <div className="action-row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void onSaveEdit()}
            >
              Save
            </button>
            <button type="button" className="btn btn-ghost" onClick={onCancelEdit}>
              Cancel
            </button>
          </div>
          <p className="muted" style={{ fontSize: '0.8rem' }}>
            Changing a trusted instruction puts it back on trial.
          </p>
        </div>
      ) : (
        <div className="action-row rule-actions">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => void onEvaluate(rule.id)}
          >
            {evaluatingId === rule.id ? 'Testing…' : 'Test this instruction'}
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={onToggleMenu}
          >
            More
          </button>
          {menuOpen ? (
            <div className="rule-more">
              <button type="button" disabled={busy} onClick={onStartEdit}>
                Edit
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onFindSimilar(rule.id)}
              >
                Find similar
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onReembed(rule.id)}
              >
                Refresh memory
              </button>
              {rule.status !== 'retired' ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onStatus(rule.id, 'retired')}
                >
                  Retire
                </button>
              ) : null}
              {rule.status !== 'rejected' ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onStatus(rule.id, 'rejected')}
                >
                  Reject
                </button>
              ) : null}
              {rule.status === 'rejected' || rule.status === 'retired' ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onStatus(rule.id, 'probation')}
                >
                  Put back on trial
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      {similarHits.length > 0 ? (
        <ul className="reason-list">
          {similarHits.map((hit) => (
            <li key={hit.id}>
              {statusLabel(hit.status)} — {hit.body.slice(0, 100)}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
