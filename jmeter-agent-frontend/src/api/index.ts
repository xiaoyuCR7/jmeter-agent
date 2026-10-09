import axios from 'axios'

export type AgentRunRequest = {
  task: string
  context?: string | null
  model?: string | null
  session_id?: string | null
  stream?: boolean
} & ({ agent_type: 'analysis'; execution_id: string; request_id?: string | null }
  | { agent_type: 'analysis'; execution_id?: null; request_id?: never }
  | { agent_type?: 'test_plan' | 'workflow'; execution_id?: never; request_id?: never })

export interface AnalysisReceiptMetadata {
  request_id: string
  execution_id: string
  state: 'running' | 'succeeded' | 'failed' | 'cancelled' | 'unknown'
  phase: string
  partial_effects_possible: boolean
  archive: Record<string, unknown> | null
  result_available: boolean
  created_at: string
  updated_at: string
}

export interface AnalysisReceipt extends AnalysisReceiptMetadata {
  result: Omit<AgentRunResult, 'task' | 'receipt'> | null
}

export interface AnalysisReceiptStatus {
  receipt: AnalysisReceipt
}

export interface RegisteredAnalysis {
  execution_id: string
  success: boolean
  error?: string | null
  phase?: 'admission' | 'history' | 'evaluation'
  error_code?: 'analysis_busy' | 'analysis_unavailable' | 'ledger_read_failed'
  fact?: Record<string, unknown> | null
  validation?: Record<string, unknown> | null
  structured?: Record<string, unknown> | null
  archive?: {
    fact_archived?: boolean
    hypotheses_archived?: string[]
    conclusion_archived?: string | null
    raw_report_archived?: string | null
    errors?: string[]
    [key: string]: unknown
  } | null
  rag?: { history_found: boolean; historical_facts_count: number; historical_hypotheses_count: number }
}

export interface AgentRunResult {
  agent_type: string
  task: string
  success: boolean
  output: string
  plan_steps: { step_id: number; description: string; success: boolean }[]
  iterations: number
  error: string | null
  duration_seconds: number
  analysis?: RegisteredAnalysis
  receipt?: AnalysisReceiptMetadata
}

export interface AgentStreamEvent {
  type: string
  message: string
  timestamp: string
  data?: (Record<string, unknown> & { analysis?: RegisteredAnalysis; receipt?: AnalysisReceiptMetadata }) | null
}

export type ProjectMemberRole = 'viewer' | 'operator'

export interface ProjectAccess {
  project_id: string
  mode: 'legacy_shared' | 'private'
  permissions: { read: boolean; write: boolean; manage: boolean }
  owner_id?: number | null
  members?: Record<string, ProjectMemberRole>
}

export interface ProjectAccessChange {
  owner_id: number
  members: Record<string, ProjectMemberRole>
}

export interface ProjectAccessPreview {
  project_id: string
  revision: string
  current: { mode: 'legacy_shared' | 'private'; owner_id: number | null; members: Record<string, ProjectMemberRole>; version: number }
  proposed: ProjectAccessChange & { mode: 'private'; version: number }
  resource_counts: Record<string, number>
}

const baseURL = import.meta.env.VITE_API_URL || '/api/v1'

const instance = axios.create({
  baseURL,
  timeout: 120000,
  withCredentials: true,
})

let refreshPromise: Promise<unknown> | null = null
let authGeneration = 0
let signingOut = false
function refreshSession() {
  if (signingOut) return Promise.reject(new Error('Signing out'))
  if (!refreshPromise) {
    refreshPromise = axios.post(`${baseURL}/auth/refresh`, null, { withCredentials: true })
      .finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

function authenticationRequired() {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('auth-required'))
}

const MAX_RETRIES = 3
const RETRY_DELAY = 1000
// Only idempotent methods are retried — retrying POST (e.g. execute) would
// trigger duplicate side effects on the server.
const RETRYABLE_METHODS = new Set(['get', 'head', 'options'])

function isRetryableError(error: any): boolean {
  if (!error.response) return true
  const status = error.response.status
  return status === 500 || status === 502 || status === 503 || status === 504 || status === 429
}

function getRetryDelay(retryCount: number): number {
  return RETRY_DELAY * Math.pow(2, retryCount - 1) + Math.random() * 500
}

instance.interceptors.request.use((config) => {
  const request = config as typeof config & { _authGeneration?: number }
  if (request._authGeneration !== undefined && request._authGeneration !== authGeneration) {
    return Promise.reject(new axios.AxiosError('Session changed', 'ERR_CANCELED', config))
  }
  request._authGeneration = authGeneration
  try {
    const saved = localStorage.getItem('jmeter-agent-lang')
    if (saved === 'en' || saved === 'zh-CN') {
      config.headers['Accept-Language'] = saved
      if (!config.params) config.params = {}
      if (!config.params.lang) config.params.lang = saved
    }
  } catch {
    // localStorage unavailable
  }
  return config
})

instance.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config
    if (!config) {
      return Promise.reject(error)
    }
    if (config._authGeneration !== authGeneration) return Promise.reject(error)
    if (error.response?.status === 401 && !config._authRetried && !config.url?.startsWith('/auth/login')) {
      config._authRetried = true
      const generation = config._authGeneration
      try {
        await refreshSession()
      } catch {
        authenticationRequired()
        return Promise.reject(error)
      }
      if (signingOut || generation !== authGeneration) return Promise.reject(error)
      return instance(config)
    }
    if (error.response?.status === 401) authenticationRequired()
    if (!config.retry) {
      config.retry = 0
    }

    const method = (config.method || 'get').toLowerCase()
    if (
      config.retry >= MAX_RETRIES ||
      !RETRYABLE_METHODS.has(method) ||
      !isRetryableError(error)
    ) {
      return Promise.reject(error)
    }

    config.retry += 1
    const delay = getRetryDelay(config.retry)

    console.log(`[API] Retrying request (${config.retry}/${MAX_RETRIES}) in ${Math.round(delay)}ms: ${config.url}`)

    await new Promise((resolve) => setTimeout(resolve, delay))
    return instance(config)
  }
)

export const api = {
  instance,
  login: async (username: string, password: string) => {
    authGeneration++
    await refreshPromise?.catch(() => undefined)
    return instance.post('/auth/login', { username, password })
  },
  logout: async () => {
    signingOut = true
    authGeneration++
    try {
      await refreshPromise?.catch(() => undefined)
      return await instance.post('/auth/logout')
    } finally { signingOut = false }
  },
  getCurrentUser: () => instance.get('/auth/me'),
  getSystemInfo: () => instance.get('/system/info'),
  getPathsConfig: () => instance.get('/system/paths'),
  updatePathsConfig: (data: any) => instance.put('/system/paths', data),

  getModels: () => instance.get('/ai/models'),
  getModelConfigs: () => instance.get('/ai/model-configs'),
  addModelConfig: (data: any) => instance.post('/ai/model-configs', data),
  updateModelConfig: (id: string, data: any) => instance.put(`/ai/model-configs/${id}`, data),
  deleteModelConfig: (id: string) => instance.delete(`/ai/model-configs/${id}`),
  testModelConfig: (modelId: string) => instance.post(`/ai/model-configs/${modelId}/test`),

  getTemplates: () => instance.get('/templates'),

  getProjects: () => instance.get('/projects'),
  getProject: (id: string) => instance.get(`/projects/${id}`),
  createProject: (data: any) => instance.post('/projects', data),
  updateProject: (id: string, data: any) => instance.put(`/projects/${id}`, data),
  deleteProject: (id: string) => instance.delete(`/projects/${id}`),
  getProjectAccess: (id: string) => instance.get<ProjectAccess>(`/projects/${encodeURIComponent(id)}/access`),
  previewProjectAccess: (id: string, data: ProjectAccessChange) =>
    instance.post<ProjectAccessPreview>(`/projects/${encodeURIComponent(id)}/access/preview`, data),
  applyProjectAccess: (id: string, data: ProjectAccessChange & { expected_revision: string }) =>
    instance.post(`/projects/${encodeURIComponent(id)}/access/apply`, data),

  generateTestPlan: (data: any) => instance.post('/test-plans/generate', data),
  generateTestPlanInProject: (projectId: string, data: any) =>
    instance.post(`/projects/${projectId}/test-plans/generate`, data),
  getTestPlans: () => instance.get('/test-plans'),
  getProjectTestPlans: (projectId: string) => instance.get(`/projects/${projectId}/test-plans`),
  getTestPlan: (id: string) => instance.get(`/test-plans/${id}`),
  deleteTestPlan: (id: string) => instance.delete(`/test-plans/${id}`),
  downloadTestPlan: (id: string) => instance.get(`/test-plans/${id}/download`, { responseType: 'blob' }),
  parseTestPlan: (id: string) => instance.get(`/test-plans/${id}/parse`),
  saveTestPlanTree: (id: string, data: any) => instance.put(`/test-plans/${id}/tree`, data),
  getJmxContent: (id: string) => instance.get(`/test-plans/${id}/jmx/content`),

  getComponents: () => instance.get('/jmeter/components'),
  getComponentChildren: (parent: string) => instance.get(`/jmeter/components/${parent}/children`),
  getFunctions: () => instance.get('/jmeter/functions'),

  executeTest: (planId: string, aiModel?: string) =>
    instance.post(`/test-plans/${planId}/execute`, null, { params: { ai_model: aiModel } }),
  getExecutions: (projectId?: string) => instance.get('/executions', { params: projectId ? { project_id: projectId } : {} }),
  getProjectExecutions: (projectId: string) => instance.get(`/projects/${projectId}/executions`),
  getExecution: (id: string) => instance.get(`/executions/${id}`),
  cancelExecution: (id: string) => instance.post(`/executions/${id}/cancel`),

  runAgent: (data: AgentRunRequest) => instance.post<AgentRunResult | AnalysisReceiptStatus>('/agent/run', data),
  getAnalysisReceipt: (requestId: string) => instance.get<AnalysisReceipt>(`/agent/analysis-requests/${encodeURIComponent(requestId)}`),
  runAgentStream: async (data: AgentRunRequest, signal?: AbortSignal): Promise<Response> => {
    signal?.throwIfAborted()
    if (signingOut) throw new Error('Session changed')
    const generation = authGeneration
    let lang = 'zh-CN'
    try {
      const saved = localStorage.getItem('jmeter-agent-lang')
      if (saved === 'en' || saved === 'zh-CN') lang = saved
    } catch { /* localStorage unavailable */ }
    const send = () => fetch(`${baseURL}/agent/run/stream`, {
      method: 'POST', credentials: 'include', signal,
      headers: { 'Content-Type': 'application/json', 'Accept-Language': lang },
      body: JSON.stringify(data)
    })
    const response = await send()
    if (response.status !== 401 || generation !== authGeneration || signingOut) return response
    signal?.throwIfAborted()
    try {
      await refreshSession()
    } catch {
      signal?.throwIfAborted()
      if (generation === authGeneration && !signingOut) authenticationRequired()
      return response
    }
    signal?.throwIfAborted()
    if (generation !== authGeneration || signingOut) return response
    // Only a rejected authentication attempt may be replayed, never an executed POST.
    await response.body?.cancel()
    signal?.throwIfAborted()
    if (generation !== authGeneration || signingOut) return response
    const retried = await send()
    if (retried.status === 401 && generation === authGeneration && !signingOut) authenticationRequired()
    return retried
  },

  runAIWorkflowStream: async (data: any) => {
    const generation = authGeneration
    const lang = localStorage.getItem('jmeter-agent-lang') || 'zh-CN'
    const send = () => fetch(`${baseURL}/ai/workflow/stream`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'Accept-Language': lang },
      body: JSON.stringify({ ...data, lang })
    })
    const response = await send()
    if (response.status !== 401) return response
    try {
      await refreshSession()
      if (signingOut || generation !== authGeneration) return response
      return await send()
    } catch {
      authenticationRequired()
      return response
    }
  },

  getProjectEnvironments: (projectId: string) => instance.get(`/projects/${projectId}/environments`),
  createEnvironment: (projectId: string, data: any) => instance.post(`/projects/${projectId}/environments`, data),
  updateEnvironment: (projectId: string, envId: string, data: any) => instance.put(`/projects/${projectId}/environments/${envId}`, data),
  deleteEnvironment: (projectId: string, envId: string) => instance.delete(`/projects/${projectId}/environments/${envId}`),
  setActiveEnvironment: (projectId: string, envId: string) => instance.put(`/projects/${projectId}/environments/active/${envId}`),

  getReport: (id: string) => instance.get(`/executions/${id}/report`),
  getLog: (id: string, lines?: number) => instance.get(`/executions/${id}/log`, { params: { lines } }),
  getHtmlReport: (id: string) => instance.get(`/executions/${id}/html-report`),
  getDashboardData: (id: string) => instance.get(`/executions/${id}/dashboard-data`),

  listAnalysisReports: (projectId?: string) => instance.get('/analysis/reports', { params: projectId ? { project_id: projectId } : {} }),
  getAnalysisReport: (executionId: string) => instance.get(`/analysis/reports/${executionId}`),
  downloadAnalysisReport: (executionId: string) => instance.get(`/analysis/reports/${executionId}/download`, { responseType: 'blob' }),

  runAutoTune: (data: any) => instance.post('/agent/autotune', null, { params: data }),

  runSelfHeal: (jmx_path: string, failed_execution_id: string, auto_re_run?: boolean) =>
    instance.post('/agent/selfheal', null, { params: { jmx_path, failed_execution_id, auto_re_run } }),

  importOpenAPI: (url: string, execute?: boolean) =>
    instance.post('/openapi/import', { url, execute }),

  detectAnomalies: (execution_id: string) =>
    instance.post('/analysis/anomalies', null, { params: { execution_id } }),

  correlateMetrics: (execution_id: string, prometheus_url?: string, promql?: string) =>
    instance.post('/analysis/correlate', null, { params: { execution_id, prometheus_url, promql } }),

  estimateCost: (data: any) => instance.post('/analysis/estimate-cost', null, { params: data }),

  analyzeDrift: (project_id?: string, days?: number) =>
    instance.post('/analysis/drift', null, { params: { project_id, days } }),
  detectDrift: (execution_id: string) =>
    instance.post('/analysis/drift', null, { params: { execution_id } }),

  compareVersions: (baseline_execution_id: string, target_execution_id: string, baseline_name?: string, target_name?: string) =>
    instance.post('/analysis/compare-versions', null, { params: { baseline_execution_id, target_execution_id, baseline_name, target_name } }),

  checkResources: () => instance.get('/system/resources'),

  getSchedules: (project_id?: string) => instance.get('/schedules', { params: project_id ? { project_id } : {} }),
  createSchedule: (data: any) => instance.post('/schedules', data),
  deleteSchedule: (id: number) => instance.delete(`/schedules/${id}`),
  toggleSchedule: (id: number) => instance.post(`/schedules/${id}/toggle`),

  getBaselines: (project_id?: string) => instance.get('/baselines', { params: project_id ? { project_id } : {} }),
  getActiveBaseline: (project_id: string, test_plan_id?: string) =>
    instance.get('/baselines/active', { params: { project_id, test_plan_id } }),
  createBaseline: ({ thresholds, metrics_snapshot, ...params }: any) =>
    instance.post('/baselines', { thresholds, metrics_snapshot }, { params }),
  deleteBaseline: (id: number) => instance.delete(`/baselines/${id}`),

  uploadCSV: (file: File, project_id?: string) => {
    const form = new FormData()
    form.append('file', file)
    return instance.post('/data/upload', form, { params: { project_id }, headers: { 'Content-Type': 'multipart/form-data' } })
  },
  getDataFiles: (project_id?: string) => instance.get('/data/files', { params: project_id ? { project_id } : {} }),
  previewCSV: (filepath: string, rows?: number) => instance.get('/data/preview', { params: { filepath, rows } }),
  deleteDataFile: (filepath: string) => instance.delete('/data/files', { params: { filepath } }),

  getMe: () => instance.get('/auth/me'),
  changePassword: (old_password: string, new_password: string) =>
    instance.post('/auth/change-password', { old_password, new_password }),
  pendingConfirmations: () => instance.get('/auth/confirmations'),
  decideConfirmation: (id: string, approved: boolean) => instance.post(`/auth/confirmations/${id}`, { approved }),
}

;(api as any).instance = instance

export default instance
