export type RiskLevel = 'low' | 'medium' | 'high' | string

export type ActionStatus =
  | 'previewed'
  | 'committed'
  | 'failed'
  | 'awaiting_approval'
  | 'auto_committed'
  | string

export interface RiskInfo {
  level: RiskLevel
  reasons: string[]
  requires_backup: boolean
}

export interface PreviewPayload {
  sql: string
  kind: string
  risk: RiskInfo
  rows_affected: number | null
  before: Record<string, unknown>[]
  after: Record<string, unknown>[]
  result_rows: Record<string, unknown>[]
  summary: Record<string, unknown>
  error: string | null
}

export interface PendingPreview {
  action_id: string
  status: ActionStatus
  sql: string
  rationale?: string
  risk: RiskInfo
  rows_affected: number | null
  before_sample?: Record<string, unknown>[]
  after_sample?: Record<string, unknown>[]
  before?: Record<string, unknown>[]
  after?: Record<string, unknown>[]
  error?: string | null
  message?: string
}

export interface ChatResponse {
  session_id: string
  message: string
  previews: PendingPreview[]
  stop_reason?: string
  model?: string
}

export interface SessionMessage {
  id: string
  role: 'user' | 'assistant' | string
  content: string
  created_at: string
}

export interface SessionAction {
  id: string
  sql_text: string
  kind: string
  risk: RiskLevel
  rows_affected: number | null
  status: ActionStatus
  diff_summary: Record<string, unknown> | null
  backup_id?: string | null
  created_at: string
  committed_at: string | null
}

export interface SessionPayload {
  session_id: string
  messages: SessionMessage[]
  actions: SessionAction[]
}

export interface Rule {
  id: string
  body: string
  trigger_text: string
  status: 'probation' | 'trusted' | 'demoted' | 'rejected' | 'retired' | string
  created_at: string
  status_changed_at: string
}

export interface RuleSearchHit extends Rule {
  dist: number
}

export interface HealthResponse {
  status: string
  database: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt?: string
}

export type MemoryTab = 'preview' | 'rules' | 'audit' | 'safety' | 'sources'

export interface Policy {
  mode: 'readonly' | 'strict' | string
  allow_writes: boolean
  block_ddl: boolean
  require_where: boolean
  max_preview_rows: number
  require_typed_confirm_for: string[]
  allowed_schemas: string[]
  allowed_tables: string[]
  confirm_phrase: string
  auto_apply_low_risk: boolean
  auto_apply_max_rows: number
  summary: string[]
}

export interface AuthUser {
  id: string
  email: string
  name: string
  email_verified: boolean
}

export interface DataConnection {
  id: string
  name: string
  engine: string
  dsn_masked: string
  active: boolean
  object_count: number
  created_at?: string
}
