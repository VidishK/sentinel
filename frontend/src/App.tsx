import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AUTH_TOKEN_KEY,
  approveAction,
  authMe,
  createRule,
  evaluateRule,
  getHealth,
  getPolicy,
  getSession,
  listRules,
  logoutAccount,
  postChat,
  reembedRule,
  rejectAction,
  searchRules,
  setRuleStatus,
  similarRules,
  updatePolicy,
  updateRule,
  verifyAccount,
} from './api'
import { AuthModal } from './components/AuthModal'
import { ChatPane } from './components/ChatPane'
import { Landing } from './components/Landing'
import { MemoryPane } from './components/MemoryPane'
import type {
  AuthUser,
  ChatMessage,
  MemoryTab,
  PendingPreview,
  Policy,
  Rule,
  RuleSearchHit,
  SessionAction,
} from './types'
import './App.css'

const SESSION_KEY = 'sentinel.sessionId'
const SCREEN_KEY = 'sentinel.screen'

const NAV_TABS: { id: MemoryTab; label: string }[] = [
  { id: 'preview', label: 'Preview' },
  { id: 'rules', label: 'Rules' },
  { id: 'audit', label: 'Audit' },
  { id: 'safety', label: 'Safety' },
  { id: 'sources', label: 'Sources' },
]

function uid() {
  return crypto.randomUUID()
}

function App() {
  const [sessionId, setSessionId] = useState<string | null>(() =>
    localStorage.getItem(SESSION_KEY),
  )
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<PendingPreview[]>([])
  const [actions, setActions] = useState<SessionAction[]>([])
  const [rules, setRules] = useState<Rule[]>([])
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [policySaving, setPolicySaving] = useState(false)
  const [tab, setTab] = useState<MemoryTab>('preview')
  const [mobileView, setMobileView] = useState<'chat' | 'memory'>('chat')
  const [health, setHealth] = useState<'loading' | 'ok' | 'bad'>('loading')
  const [database, setDatabase] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [evaluatingId, setEvaluatingId] = useState<string | null>(null)
  const [actingRuleId, setActingRuleId] = useState<string | null>(null)
  const [creatingRule, setCreatingRule] = useState(false)
  const [searchHits, setSearchHits] = useState<RuleSearchHit[]>([])
  const [searching, setSearching] = useState(false)
  const [similarFor, setSimilarFor] = useState<string | null>(null)
  const [similarHits, setSimilarHits] = useState<RuleSearchHit[]>([])
  const [screen, setScreen] = useState<'landing' | 'agent'>(() => {
    if (!localStorage.getItem(AUTH_TOKEN_KEY)) return 'landing'
    return localStorage.getItem(SCREEN_KEY) === 'agent' ? 'agent' : 'landing'
  })
  const [authOpen, setAuthOpen] = useState(false)
  const [user, setUser] = useState<AuthUser | null>(null)

  function openAgent() {
    if (!user && !localStorage.getItem(AUTH_TOKEN_KEY)) {
      setAuthOpen(true)
      return
    }
    setScreen('agent')
    localStorage.setItem(SCREEN_KEY, 'agent')
  }

  function openLanding() {
    setScreen('landing')
    localStorage.setItem(SCREEN_KEY, 'landing')
  }

  function handleAuthenticated(next: AuthUser) {
    setUser(next)
    setAuthOpen(false)
    localStorage.removeItem(SESSION_KEY)
    setSessionId(null)
    setMessages([])
    setPending([])
    setActions([])
    setScreen('agent')
    localStorage.setItem(SCREEN_KEY, 'agent')
    setToast(`Signed in as ${next.email}`)
  }

  async function handleLogout() {
    try {
      await logoutAccount()
    } catch {
      // still clear locally
    }
    localStorage.removeItem(AUTH_TOKEN_KEY)
    setUser(null)
    openLanding()
  }

  const refreshRules = useCallback(async () => {
    const data = await listRules()
    setRules(data.rules)
  }, [])

  const refreshPolicy = useCallback(async () => {
    const data = await getPolicy()
    setPolicy(data)
  }, [])

  const refreshSession = useCallback(async (id: string) => {
    const data = await getSession(id)
    setActions(data.actions)
    setMessages(
      data.messages.map((message) => ({
        id: message.id,
        role: message.role === 'user' ? 'user' : 'assistant',
        content: message.content,
        createdAt: message.created_at,
      })),
    )
    setPending((current) =>
      current.filter((preview) =>
        data.actions.some(
          (action) =>
            action.id === preview.action_id && action.status === 'previewed',
        ),
      ),
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    async function check() {
      try {
        const data = await getHealth()
        if (cancelled) return
        setHealth(data.status === 'ok' ? 'ok' : 'bad')
        setDatabase(data.database)
      } catch {
        if (!cancelled) setHealth('bad')
      }
    }
    void check()
    const timer = window.setInterval(() => void check(), 8000)
    const onFocus = () => void check()
    window.addEventListener('focus', onFocus)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [])

  useEffect(() => {
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    if (!token) {
      if (localStorage.getItem(SCREEN_KEY) === 'agent') {
        localStorage.setItem(SCREEN_KEY, 'landing')
        setScreen('landing')
      }
      return
    }
    authMe()
      .then((data) => setUser(data.user))
      .catch(() => {
        localStorage.removeItem(AUTH_TOKEN_KEY)
        setUser(null)
        setScreen('landing')
        localStorage.setItem(SCREEN_KEY, 'landing')
      })
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const verify = params.get('verify')
    if (!verify) return
    verifyAccount(verify)
      .then((data) => {
        localStorage.setItem(AUTH_TOKEN_KEY, data.token)
        handleAuthenticated(data.user)
        window.history.replaceState({}, '', window.location.pathname)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Verification failed')
        setAuthOpen(true)
      })
    // handleAuthenticated is stable enough for this mount-only verify.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!user) return
    refreshRules().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : 'Failed to load rules')
    })
    refreshPolicy().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : 'Failed to load policy')
    })
  }, [user, refreshRules, refreshPolicy])

  useEffect(() => {
    if (!sessionId || !user) return
    refreshSession(sessionId).catch((err: unknown) => {
      if (err instanceof Error && /not found|404/i.test(err.message)) {
        localStorage.removeItem(SESSION_KEY)
        setSessionId(null)
        setMessages([])
        setActions([])
        return
      }
      setError(err instanceof Error ? err.message : 'Failed to load session')
    })
  }, [sessionId, user, refreshSession])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const statusLabel = useMemo(() => {
    if (health === 'loading') return 'Checking cluster…'
    if (health === 'ok') return `Connected · ${database ?? 'database'}`
    return 'API unreachable'
  }, [health, database])

  const policyBadge = useMemo(() => {
    if (!policy) return 'Loading policy…'
    if (policy.mode === 'readonly' || !policy.allow_writes) return 'Read-only'
    if (policy.auto_apply_low_risk !== false) return 'Auto low-risk'
    return 'Strict writes'
  }, [policy])

  async function handleSend(text?: string) {
    const content = (text ?? draft).trim()
    if (!content || busy) return

    setError(null)
    setDraft('')
    setBusy(true)
    setMessages((current) => [
      ...current,
      { id: uid(), role: 'user', content },
    ])

    try {
      const response = await postChat(content, sessionId)
      setSessionId(response.session_id)
      localStorage.setItem(SESSION_KEY, response.session_id)
      setMessages((current) => [
        ...current,
        { id: uid(), role: 'assistant', content: response.message },
      ])
      if (response.previews?.length) {
        const waiting = response.previews.filter(
          (preview) => preview.status === 'awaiting_approval' || preview.status === 'previewed',
        )
        const auto = response.previews.filter(
          (preview) => preview.status === 'auto_committed',
        )
        if (waiting.length) {
          setPending((current) => {
            const map = new Map(current.map((item) => [item.action_id, item]))
            for (const preview of waiting) {
              map.set(preview.action_id, preview)
            }
            return [...map.values()]
          })
          setTab('preview')
          setMobileView('memory')
        } else if (auto.length) {
          setToast('Low-risk change applied automatically. See Audit.')
          setTab('audit')
          setMobileView('memory')
        }
      }
      await refreshSession(response.session_id)
      await refreshRules()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Chat failed'
      setError(message)
      setMessages((current) => [
        ...current,
        {
          id: uid(),
          role: 'assistant',
          content: `I could not complete that turn: ${message}`,
        },
      ])
    } finally {
      setBusy(false)
    }
  }

  async function handleApprove(actionId: string, confirmation?: string) {
    setApprovingId(actionId)
    setError(null)
    try {
      await approveAction(actionId, confirmation)
      setPending((current) =>
        current.filter((item) => item.action_id !== actionId),
      )
      setToast('Change committed. Audit trail updated.')
      setTab('audit')
      if (sessionId) await refreshSession(sessionId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approve failed')
    } finally {
      setApprovingId(null)
    }
  }

  async function handleReject(actionId: string) {
    setApprovingId(actionId)
    setError(null)
    try {
      await rejectAction(actionId)
      setPending((current) =>
        current.filter((item) => item.action_id !== actionId),
      )
      setToast('Change rejected. Nothing was committed.')
      setTab('audit')
      if (sessionId) await refreshSession(sessionId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reject failed')
    } finally {
      setApprovingId(null)
    }
  }

  async function handleUpdatePolicy(patch: Partial<Policy>) {
    setPolicySaving(true)
    setError(null)
    try {
      const next = await updatePolicy(patch)
      setPolicy(next)
      setToast('Safety policy updated.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Policy update failed')
    } finally {
      setPolicySaving(false)
    }
  }

  async function handleCreateRule(body: string, trigger: string) {
    setCreatingRule(true)
    setError(null)
    try {
      await createRule(body, trigger)
      setToast('Rule saved in probation.')
      await refreshRules()
      setTab('rules')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create rule')
      throw err
    } finally {
      setCreatingRule(false)
    }
  }

  async function handleEvaluate(ruleId: string) {
    setEvaluatingId(ruleId)
    setError(null)
    try {
      const result = await evaluateRule(ruleId)
      const toStatus =
        typeof result.to_status === 'string' ? result.to_status : null
      const promoted = result.promoted === true
      setToast(
        toStatus
          ? `Rule ${promoted ? 'promoted' : 'decided'}: ${toStatus}.`
          : 'Evaluation completed.',
      )
      await refreshRules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Evaluation failed')
    } finally {
      setEvaluatingId(null)
    }
  }

  async function handleSearchRules(query: string, status?: string) {
    setSearching(true)
    setError(null)
    try {
      const result = await searchRules(query, status)
      setSearchHits(result.rules)
      setToast(`${result.rules.length} vector matches`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  async function handleUpdateRule(ruleId: string, body: string, trigger: string) {
    setActingRuleId(ruleId)
    setError(null)
    try {
      const result = await updateRule(ruleId, body, trigger)
      setToast(
        result.demoted
          ? 'Saved & re-embedded. Trusted rule demoted to probation.'
          : 'Rule updated and re-embedded.',
      )
      await refreshRules()
      setSearchHits([])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
      throw err
    } finally {
      setActingRuleId(null)
    }
  }

  async function handleRuleStatus(ruleId: string, status: string) {
    setActingRuleId(ruleId)
    setError(null)
    try {
      await setRuleStatus(ruleId, status)
      setToast(`Rule marked ${status}.`)
      await refreshRules()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Status change failed')
    } finally {
      setActingRuleId(null)
    }
  }

  async function handleReembedRule(ruleId: string) {
    setActingRuleId(ruleId)
    setError(null)
    try {
      await reembedRule(ruleId)
      setToast('Instruction memory refreshed.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Re-embed failed')
    } finally {
      setActingRuleId(null)
    }
  }

  async function handleFindSimilar(ruleId: string) {
    setActingRuleId(ruleId)
    setError(null)
    try {
      const result = await similarRules(ruleId)
      setSimilarFor(ruleId)
      setSimilarHits(result.rules)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Similar search failed')
    } finally {
      setActingRuleId(null)
    }
  }

  function handleReset() {
    localStorage.removeItem(SESSION_KEY)
    setSessionId(null)
    setMessages([])
    setPending([])
    setActions([])
    setDraft('')
    setError(null)
    setTab('preview')
    setMobileView('chat')
  }

  function openMemory(next: MemoryTab) {
    setTab(next)
    setMobileView('memory')
  }

  if (screen === 'landing') {
    return (
      <div className="app-shell is-landing">
        <div className="page-glow" aria-hidden="true">
          <div className="glow-core" />
          <div className="glow-ring r1" />
          <div className="glow-ring r2" />
          <div className="glow-ring r3" />
        </div>
        <Landing onOpenAgent={openAgent} onLogIn={() => setAuthOpen(true)} />
        <AuthModal
          open={authOpen}
          onClose={() => setAuthOpen(false)}
          onAuthenticated={handleAuthenticated}
        />
        {error ? (
          <div className="error-banner global" role="alert">
            {error}
          </div>
        ) : null}
        {toast ? (
          <div className="toast" role="status">
            {toast}
          </div>
        ) : null}
      </div>
    )
  }

  return (
    <div className="app-shell is-agent">
      <header className="top-nav dashboard-nav">
        <button type="button" className="logo" onClick={openLanding}>
          <span className="logo-mark">S</span>
          <span className="logo-text">
            <strong>Sentinel</strong>
            <span>Agent</span>
          </span>
        </button>
        <nav className="nav-links" aria-label="Dashboard">
          {NAV_TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={tab === item.id ? 'active' : undefined}
              onClick={() => openMemory(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="nav-actions">
          <div className={`status-chip ${health}`} aria-live="polite">
            <span className="dot" />
            {statusLabel}
          </div>
          <button
            type="button"
            className="policy-badge"
            onClick={() => openMemory('safety')}
          >
            {policyBadge}
          </button>
          {user ? (
            <button
              type="button"
              className="status-chip"
              title="Log out"
              onClick={() => void handleLogout()}
            >
              {user.name || user.email}
            </button>
          ) : (
            <button type="button" className="status-chip" onClick={() => setAuthOpen(true)}>
              Log in
            </button>
          )}
        </div>
      </header>

      <div className={`workspace show-${mobileView}`}>
        <div className="mobile-tabs">
          <button
            type="button"
            className={mobileView === 'chat' ? 'active' : undefined}
            onClick={() => setMobileView('chat')}
          >
            Chat
          </button>
          <button
            type="button"
            className={mobileView === 'memory' ? 'active' : undefined}
            onClick={() => setMobileView('memory')}
          >
            Memory
          </button>
        </div>

        <ChatPane
          messages={messages}
          draft={draft}
          busy={busy}
          sessionId={sessionId}
          onDraftChange={setDraft}
          onSend={handleSend}
          onReset={handleReset}
        />

        <MemoryPane
          tab={tab}
          onTabChange={(next) => {
            setTab(next)
            setMobileView('memory')
          }}
          pending={pending}
          actions={actions}
          rules={rules}
          policy={policy}
          policySaving={policySaving}
          approvingId={approvingId}
          evaluatingId={evaluatingId}
          actingRuleId={actingRuleId}
          searchHits={searchHits}
          searching={searching}
          similarFor={similarFor}
          similarHits={similarHits}
          onApprove={handleApprove}
          onReject={handleReject}
          onCreateRule={handleCreateRule}
          onEvaluate={handleEvaluate}
          onSearchRules={handleSearchRules}
          onClearRuleSearch={() => setSearchHits([])}
          onUpdateRule={handleUpdateRule}
          onRuleStatus={handleRuleStatus}
          onReembedRule={handleReembedRule}
          onFindSimilar={handleFindSimilar}
          onUpdatePolicy={handleUpdatePolicy}
          creatingRule={creatingRule}
        />
      </div>

      {error ? (
        <div className="error-banner global" role="alert">
          {error}
        </div>
      ) : null}

      {toast ? (
        <div className="toast" role="status">
          {toast}
        </div>
      ) : null}

      <AuthModal
        open={authOpen}
        onClose={() => setAuthOpen(false)}
        onAuthenticated={handleAuthenticated}
      />
    </div>
  )
}

export default App
