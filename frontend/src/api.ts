import type {
  AuthUser,
  ChatResponse,
  HealthResponse,
  PendingPreview,
  Policy,
  Rule,
  SessionPayload,
} from './types'

const API_BASE = '/api'
export const AUTH_TOKEN_KEY = 'sentinel.authToken'

function authHeader(): Record<string, string> {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
      ...(init?.headers ?? {}),
    },
  })

    if (response.status === 401) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
    }
    if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (body.detail != null) detail = JSON.stringify(body.detail)
    } catch {
      // keep status text
    }
    throw new Error(detail)
  }

  return response.json() as Promise<T>
}

export function getHealth() {
  return request<HealthResponse>('/health')
}

export function getPolicy() {
  return request<Policy>('/policy')
}

export function updatePolicy(patch: Partial<Policy>) {
  return request<Policy>('/policy', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

export function postChat(message: string, sessionId?: string | null) {
  return request<ChatResponse>('/chat', {
    method: 'POST',
    body: JSON.stringify({
      message,
      session_id: sessionId ?? null,
    }),
  })
}

export function getSession(sessionId: string) {
  return request<SessionPayload>(`/sessions/${sessionId}/messages`)
}

export function approveAction(actionId: string, confirmation?: string) {
  return request<{ action_id: string; status?: string; message?: string }>(
    '/actions/approve',
    {
      method: 'POST',
      body: JSON.stringify({
        action_id: actionId,
        confirmation: confirmation ?? null,
      }),
    },
  )
}

export function rejectAction(actionId: string, reason?: string) {
  return request<{ action_id: string; status: string }>('/actions/reject', {
    method: 'POST',
    body: JSON.stringify({
      action_id: actionId,
      reason: reason ?? 'rejected in UI',
    }),
  })
}

export function previewSql(sql: string, sessionId?: string | null) {
  return request<{
    action_id: string
    session_id: string
    preview: PendingPreview & {
      before: Record<string, unknown>[]
      after: Record<string, unknown>[]
      result_rows: Record<string, unknown>[]
      summary: Record<string, unknown>
    }
  }>('/actions/preview', {
    method: 'POST',
    body: JSON.stringify({
      sql,
      session_id: sessionId ?? null,
      request: 'manual preview',
    }),
  })
}

export function listRules(status?: string) {
  const q = status ? `?status=${encodeURIComponent(status)}` : ''
  return request<{ rules: Rule[] }>(`/rules${q}`)
}

export async function createRule(body: string, triggerText: string) {
  const payload = await request<{ rule: Rule; near_duplicates?: unknown[] }>(
    '/rules',
    {
      method: 'POST',
      body: JSON.stringify({ body, trigger_text: triggerText }),
    },
  )
  return payload.rule
}

export function evaluateRule(ruleId: string, taskFamily?: string) {
  return request<Record<string, unknown>>(`/rules/${ruleId}/evaluate`, {
    method: 'POST',
    body: JSON.stringify(taskFamily ? { task_family: taskFamily } : {}),
  })
}

export function searchRules(query: string, status?: string) {
  const params = new URLSearchParams({ q: query })
  if (status) params.set('status', status)
  return request<{ query: string; rules: import('./types').RuleSearchHit[] }>(
    `/rules/search?${params.toString()}`,
  )
}

export function updateRule(ruleId: string, body: string, triggerText: string) {
  return request<{
    rule: import('./types').Rule
    near_duplicates?: unknown[]
    demoted?: boolean
  }>(`/rules/${ruleId}`, {
    method: 'PATCH',
    body: JSON.stringify({ body, trigger_text: triggerText }),
  })
}

export function setRuleStatus(ruleId: string, status: string, reason?: string) {
  return request<{ rule: import('./types').Rule }>(`/rules/${ruleId}/status`, {
    method: 'POST',
    body: JSON.stringify({ status, reason: reason ?? 'manual UI' }),
  })
}

export function reembedRule(ruleId: string) {
  return request<{ rule: import('./types').Rule; reembedded: boolean }>(
    `/rules/${ruleId}/reembed`,
    { method: 'POST', body: '{}' },
  )
}

export function similarRules(ruleId: string) {
  return request<{ rules: import('./types').RuleSearchHit[] }>(
    `/rules/${ruleId}/similar`,
  )
}

export function reindexSchemaMemory() {
  return request<{ upserted: number }>('/memory/reindex-schema', {
    method: 'POST',
    body: '{}',
  })
}

export function listBackups() {
  return request<{
    cluster: string
    backups: { id: string; as_of_time?: string }[]
  }>('/backups')
}

export function listConnections() {
  return request<{ connections: import('./types').DataConnection[] }>(
    '/connections',
  )
}

export function createConnection(name: string, engine: string, dsn: string) {
  return request<import('./types').DataConnection>('/connections', {
    method: 'POST',
    body: JSON.stringify({ name, engine, dsn }),
  })
}

export function activateConnection(id: string) {
  return request<import('./types').DataConnection>(
    `/connections/${id}/activate`,
    { method: 'POST', body: '{}' },
  )
}

export function refreshConnections() {
  return request<import('./types').DataConnection>('/connections/refresh', {
    method: 'POST',
    body: '{}',
  })
}

export function deleteConnection(id: string) {
  return request<{ status: string }>(`/connections/${id}`, {
    method: 'DELETE',
  })
}

export function authConfig() {
  return request<{ google_enabled: boolean; google_client_id: string }>(
    '/auth/config',
  )
}

export function signupAccount(email: string, password: string, name?: string) {
  return request<{
    status: string
    email: string
    message: string
    email_sent?: boolean
    send_error?: string
    verify_url?: string
    preview?: {
      from: string
      to: string
      subject: string
      verify_url: string
    }
  }>('/auth/signup', {
    method: 'POST',
    body: JSON.stringify({ email, password, name: name || null }),
  })
}

export function loginAccount(email: string, password: string) {
  return request<{ token: string; user: AuthUser }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function loginGoogle(idToken: string) {
  return request<{ token: string; user: AuthUser }>('/auth/google', {
    method: 'POST',
    body: JSON.stringify({ id_token: idToken }),
  })
}

export function verifyAccount(token: string) {
  return request<{ token: string; user: AuthUser }>('/auth/verify', {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

export function authMe() {
  return request<{ user: AuthUser }>('/auth/me')
}

export function logoutAccount() {
  return request<{ status: string }>('/auth/logout', {
    method: 'POST',
    body: '{}',
  })
}
