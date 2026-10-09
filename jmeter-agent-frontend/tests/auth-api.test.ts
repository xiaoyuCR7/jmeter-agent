// @vitest-environment node
import { describe, it, expect, vi, afterEach } from 'vitest'
import axios, { AxiosError, AxiosHeaders } from 'axios'
import { api, type RegisteredAnalysis } from '../src/api'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('authenticated browser transport', () => {
  it.each([
    ['analysis_busy', 'admission'], ['analysis_unavailable', 'admission'],
    ['ledger_read_failed', 'history'], ['ledger_read_failed', 'evaluation']
  ] as const)('returns %s at %s without replaying POSTs or streams', async (error_code, phase) => {
    const analysis: RegisteredAnalysis = { execution_id: 'run-A', success: false, phase, error_code }
    const payload = { success: false, error: 'retry later', analysis }
    let posts = 0
    api.instance.defaults.adapter = async config => {
      posts++
      return { status: 200, statusText: 'OK', data: payload, headers: {}, config }
    }
    const request = { agent_type: 'analysis' as const, task: 'explain', execution_id: 'run-A' }
    expect((await api.runAgent(request)).data).toEqual(payload)
    expect(posts).toBe(1)
    const body = `data: ${JSON.stringify({ type: 'done', data: payload })}\n\n`
    const fetch = vi.fn().mockResolvedValue(new Response(body, { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const refresh = vi.spyOn(axios, 'post')
    expect(await (await api.runAgentStream(request)).text()).toBe(body)
    expect(fetch).toHaveBeenCalledOnce()
    expect(refresh).not.toHaveBeenCalled()
  })

  it('registered Agent streams send the ID, cookies, language and cancellation signal', async () => {
    vi.stubGlobal('localStorage', { getItem: () => 'en' })
    const fetch = vi.fn().mockResolvedValue({ status: 200 })
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    const data = { agent_type: 'analysis' as const, task: 'explain', execution_id: 'run-A' }
    await api.runAgentStream(data, controller.signal)
    expect(fetch).toHaveBeenCalledWith('/api/v1/agent/run/stream', {
      method: 'POST', credentials: 'include', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'Accept-Language': 'en' }, body: JSON.stringify(data)
    })
  })

  it.each([202, 409, 500, 503])('registered Agent streams do not replay HTTP %s', async status => {
    const fetch = vi.fn().mockResolvedValue({ status })
    vi.stubGlobal('fetch', fetch)
    const refresh = vi.spyOn(axios, 'post')
    expect((await api.runAgentStream({ agent_type: 'analysis', task: 'task', execution_id: 'run-A', request_id: 'key-A' })).status).toBe(status)
    expect(fetch).toHaveBeenCalledOnce()
    expect(refresh).not.toHaveBeenCalled()
  })

  it('registered Agent POSTs are not replayed after a server failure', async () => {
    let calls = 0
    api.instance.defaults.adapter = async config => {
      calls++
      throw new AxiosError('failed', 'ERR_BAD_RESPONSE', config, null,
        { status: 503, statusText: 'Unavailable', data: {}, headers: new AxiosHeaders(), config })
    }
    await expect(api.runAgent({ agent_type: 'analysis', task: 'task', execution_id: 'run-A' })).rejects.toThrow('failed')
    expect(calls).toBe(1)
  })

  it('registered streams refresh only once and report a repeated unauthorized response', async () => {
    const dispatch = vi.fn()
    vi.stubGlobal('window', { dispatchEvent: dispatch })
    vi.stubGlobal('CustomEvent', class { constructor(public type: string) {} })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 401 }))
    const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    expect((await api.runAgentStream({ agent_type: 'analysis', task: 'task' })).status).toBe(401)
    expect(refresh).toHaveBeenCalledOnce()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(dispatch).toHaveBeenCalledOnce()
  })

  it('aborting during stream refresh prevents a second POST and preserves cancellation', async () => {
    let release!: () => void
    const refresh = vi.spyOn(axios, 'post').mockImplementation(() => new Promise(resolve => {
      release = () => resolve({ data: {} })
    }))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 401 }))
    const controller = new AbortController()
    const result = api.runAgentStream({ agent_type: 'analysis', task: 'task' }, controller.signal)
    const rejected = expect(result).rejects.toMatchObject({ name: 'AbortError' })
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce())
    controller.abort()
    release()
    await rejected
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('a stale stream 401 after logout cannot refresh or replay the old task', async () => {
    let release!: () => void
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise(resolve => {
      release = () => resolve({ status: 401 })
    })))
    const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    api.instance.defaults.adapter = async config => ({ status: 200, statusText: 'OK', data: {}, headers: {}, config })
    const result = api.runAgentStream({ agent_type: 'analysis', task: 'task' })
    await api.logout()
    release()
    expect((await result).status).toBe(401)
    expect(refresh).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('logout while releasing a rejected response body prevents a stale stream replay', async () => {
    let release!: () => void
    const cancel = vi.fn().mockImplementation(() => new Promise(resolve => { release = () => resolve(undefined) }))
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 401, body: { cancel } }))
    vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    api.instance.defaults.adapter = async config => ({ status: 200, statusText: 'OK', data: {}, headers: {}, config })
    const result = api.runAgentStream({ agent_type: 'analysis', task: 'task' })
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledOnce())
    await api.logout()
    release()
    expect((await result).status).toBe(401)
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('a stream network error is propagated without replay or authentication changes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network failed')))
    const refresh = vi.spyOn(axios, 'post')
    await expect(api.runAgentStream({ agent_type: 'analysis', task: 'task' })).rejects.toThrow('network failed')
    expect(fetch).toHaveBeenCalledOnce()
    expect(refresh).not.toHaveBeenCalled()
  })

  it('a pre-aborted stream never sends a request', async () => {
    vi.stubGlobal('fetch', vi.fn())
    const controller = new AbortController()
    controller.abort()
    await expect(api.runAgentStream({ agent_type: 'analysis', task: 'task' }, controller.signal))
      .rejects.toMatchObject({ name: 'AbortError' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('never replays an old POST when its 401 arrives after logout and another login', async () => {
    let rejectOld!: () => void
    const calls: string[] = []
    const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    api.instance.defaults.adapter = async config => {
      calls.push(config.url || '')
      if (config.url === '/projects' && calls.filter(path => path === '/projects').length === 1) {
        return new Promise((_resolve, reject) => {
          rejectOld = () => reject(new AxiosError('expired', 'ERR_BAD_REQUEST', config, null,
            { status: 401, statusText: 'Unauthorized', data: {}, headers: new AxiosHeaders(), config }))
        })
      }
      return { status: 200, statusText: 'OK', data: {}, headers: {}, config }
    }
    const old = api.createProject({ name: 'old-user-action' }).catch(() => null)
    await vi.waitFor(() => expect(rejectOld).toBeDefined())
    await api.logout()
    await api.login('another-user', 'test-only')
    rejectOld()
    await old
    expect(refresh).not.toHaveBeenCalled()
    expect(calls).toEqual(['/projects', '/auth/logout', '/auth/login'])
  })

  it('logout waits for refresh and prevents the old request replay', async () => {
    let release!: () => void
    const delayed = new Promise(resolve => { release = () => resolve({ data: {} }) })
    const refresh = vi.spyOn(axios, 'post').mockReturnValue(delayed as any)
    const calls: string[] = []
    api.instance.defaults.adapter = async config => {
      calls.push(config.url || '')
      if (config.url === '/projects') throw new AxiosError('expired', 'ERR_BAD_REQUEST', config, null,
        { status: 401, statusText: 'Unauthorized', data: {}, headers: new AxiosHeaders(), config })
      return { status: 200, statusText: 'OK', data: {}, headers: {}, config }
    }
    const request = api.getProjects().catch(() => null)
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce())
    const logout = api.logout()
    await Promise.resolve()
    expect(calls).not.toContain('/auth/logout')
    release()
    await Promise.all([request, logout])
    expect(calls).toEqual(['/projects', '/auth/logout'])
  })
  it('sends password changes in the JSON body, never URL parameters', async () => {
    const configs: any[] = []
    api.instance.defaults.adapter = async config => {
      configs.push(config)
      return { status: 200, statusText: 'OK', data: {}, headers: {}, config }
    }
    await api.changePassword('old-password', 'new-password')
    expect(JSON.parse(configs[0].data)).toEqual({ old_password: 'old-password', new_password: 'new-password' })
    expect(configs[0].params?.new_password).toBeUndefined()
  })
  it('sends cookies for API requests', async () => {
    const configs: any[] = []
    api.instance.defaults.adapter = async config => {
      configs.push(config)
      return { status: 200, statusText: 'OK', data: {}, headers: {}, config }
    }
    await api.getProjects()
    expect(configs[0].withCredentials).toBe(true)
  })

  it('refreshes once before retrying a rejected request', async () => {
    let calls = 0
    api.instance.defaults.adapter = async config => {
      calls++
      if (calls === 1) throw new AxiosError('expired', 'ERR_BAD_REQUEST', config, null,
        { status: 401, statusText: 'Unauthorized', data: {}, headers: new AxiosHeaders(), config })
      return { status: 200, statusText: 'OK', data: { ok: true }, headers: {}, config }
    }
    const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    expect((await api.getProjects()).data.ok).toBe(true)
    expect(refresh).toHaveBeenCalledOnce()
    expect(calls).toBe(2)
  })

  it('also authenticates streaming requests', async () => {
    vi.stubGlobal('localStorage', { getItem: () => null })
    const fetch = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetch)
    await api.runAIWorkflowStream({ task: 'test' })
    expect(fetch.mock.calls[0][1].credentials).toBe('include')
    vi.unstubAllGlobals()
  })
})
