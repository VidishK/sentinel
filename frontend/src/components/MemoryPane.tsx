import { AuditPanel } from './AuditPanel'
import { PreviewPanel } from './PreviewPanel'
import { RulesPanel } from './RulesPanel'
import { SafetyPanel } from './SafetyPanel'
import { SourcesPanel } from './SourcesPanel'
import type {
  MemoryTab,
  PendingPreview,
  Policy,
  Rule,
  RuleSearchHit,
  SessionAction,
} from '../types'

interface MemoryPaneProps {
  tab: MemoryTab
  onTabChange: (tab: MemoryTab) => void
  pending: PendingPreview[]
  actions: SessionAction[]
  rules: Rule[]
  policy: Policy | null
  policySaving: boolean
  approvingId: string | null
  evaluatingId: string | null
  actingRuleId: string | null
  searchHits: RuleSearchHit[]
  searching: boolean
  similarFor: string | null
  similarHits: RuleSearchHit[]
  onApprove: (actionId: string, confirmation?: string) => void
  onReject: (actionId: string) => void
  onCreateRule: (body: string, trigger: string) => Promise<void>
  onEvaluate: (ruleId: string) => Promise<void>
  onSearchRules: (query: string, status?: string) => Promise<void>
  onClearRuleSearch: () => void
  onUpdateRule: (ruleId: string, body: string, trigger: string) => Promise<void>
  onRuleStatus: (ruleId: string, status: string) => Promise<void>
  onReembedRule: (ruleId: string) => Promise<void>
  onFindSimilar: (ruleId: string) => Promise<void>
  onUpdatePolicy: (patch: Partial<Policy>) => Promise<void>
  creatingRule: boolean
}

export function MemoryPane({
  tab,
  onTabChange,
  pending,
  actions,
  rules,
  policy,
  policySaving,
  approvingId,
  evaluatingId,
  actingRuleId,
  searchHits,
  searching,
  similarFor,
  similarHits,
  onApprove,
  onReject,
  onCreateRule,
  onEvaluate,
  onSearchRules,
  onClearRuleSearch,
  onUpdateRule,
  onRuleStatus,
  onReembedRule,
  onFindSimilar,
  onUpdatePolicy,
  creatingRule,
}: MemoryPaneProps) {
  return (
    <section className="panel memory" aria-label="Live memory">
      <div className="panel-header">
        <h2>Live memory</h2>
        <span className="meta">preview · rules · audit · safety · sources</span>
      </div>

      <div className="memory-tabs tabs-5" role="tablist" aria-label="Memory sections">
        {(
          [
            ['preview', 'Preview'],
            ['rules', 'Rules'],
            ['audit', 'Audit'],
            ['safety', 'Safety'],
            ['sources', 'Sources'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'active' : undefined}
            onClick={() => onTabChange(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="memory-body" key={tab}>
        {tab === 'preview' ? (
          <PreviewPanel
            pending={pending}
            actions={actions}
            busyId={approvingId}
            confirmLevels={policy?.require_typed_confirm_for ?? ['medium', 'high']}
            confirmPhrase={policy?.confirm_phrase ?? 'COMMIT'}
            onApprove={onApprove}
            onReject={onReject}
          />
        ) : null}

        {tab === 'rules' ? (
          <RulesPanel
            rules={rules}
            busy={creatingRule}
            evaluatingId={evaluatingId}
            actingId={actingRuleId}
            searchHits={searchHits}
            searching={searching}
            onCreate={onCreateRule}
            onEvaluate={onEvaluate}
            onSearch={onSearchRules}
            onClearSearch={onClearRuleSearch}
            onUpdate={onUpdateRule}
            onStatus={onRuleStatus}
            onReembed={onReembedRule}
            onFindSimilar={onFindSimilar}
            similarFor={similarFor}
            similarHits={similarHits}
          />
        ) : null}

        {tab === 'audit' ? <AuditPanel actions={actions} /> : null}

        {tab === 'safety' ? (
          <SafetyPanel
            policy={policy}
            saving={policySaving}
            onUpdate={onUpdatePolicy}
          />
        ) : null}

        {tab === 'sources' ? <SourcesPanel /> : null}
      </div>
    </section>
  )
}
