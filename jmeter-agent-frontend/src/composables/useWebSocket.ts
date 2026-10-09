/**
 * WebSocket composable with automatic reconnection + exponential backoff + heartbeat.
 *
 * Usage:
 *   const { status, connect, send, close } = useWebSocket('ws://localhost:8000/ws/executions/abc123')
 *   status.value // 'connecting' | 'connected' | 'disconnected' | 'reconnecting'
 */

import { ref, onUnmounted, type Ref } from 'vue'

export type WSStatus = 'connecting' | 'connected' | 'disconnected' | 'reconnecting'

export interface WSOptions {
  maxReconnectAttempts?: number
  baseDelay?: number
  maxDelay?: number
  heartbeatInterval?: number
  heartbeatTimeout?: number
  connectTimeout?: number
  onMessage?: (data: any) => void
  onStatusChange?: (status: WSStatus) => void
  queryParams?: Record<string, string>
}

export function useWebSocket(url: string | (() => string), options: WSOptions = {}) {
  const {
    maxReconnectAttempts = 10,
    baseDelay = 1000,
    maxDelay = 30000,
    heartbeatInterval = 30000,
    heartbeatTimeout = 60000,
    connectTimeout = 10000,
    onMessage,
    onStatusChange,
    queryParams,
  } = options

  const status = ref<WSStatus>('disconnected')
  const error = ref<string | null>(null)
  const reconnectAttempt = ref(0)
  const lastMessage = ref<any>(null)

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let heartbeatTimeoutTimer: ReturnType<typeof setTimeout> | null = null
  let connectTimeoutTimer: ReturnType<typeof setTimeout> | null = null
  let intentionallyClosed = false
  let disposed = false

  function buildUrl(): string {
    const base = typeof url === 'function' ? url() : url
    if (!queryParams || Object.keys(queryParams).length === 0) return base
    const params = new URLSearchParams(queryParams).toString()
    return `${base}${base.includes('?') ? '&' : '?'}${params}`
  }

  function setStatus(newStatus: WSStatus) {
    status.value = newStatus
    onStatusChange?.(newStatus)
  }

  function getDelay(): number {
    const exp = Math.min(Math.pow(2, reconnectAttempt.value) * baseDelay, maxDelay)
    const jitter = Math.random() * 1000
    return Math.min(exp + jitter, maxDelay)
  }

  function scheduleReconnect() {
    if (intentionallyClosed) return
    if (reconnectAttempt.value >= maxReconnectAttempts) {
      error.value = `WebSocket reconnection failed after ${maxReconnectAttempts} attempts`
      setStatus('disconnected')
      return
    }

    const delay = getDelay()
    reconnectAttempt.value++
    setStatus('reconnecting')

    console.log(`[WS] Reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttempt.value}/${maxReconnectAttempts})...`)
    reconnectTimer = setTimeout(() => connect(), delay)
  }

  let lastPingTime = 0

  function startHeartbeat() {
    stopHeartbeat()
    lastPingTime = Date.now()
    heartbeatTimer = setInterval(() => {
      if (Date.now() - lastPingTime > heartbeatTimeout) {
        console.error('[WS] Heartbeat timeout - no ping received from server')
        error.value = 'WebSocket heartbeat timeout'
        if (ws && !intentionallyClosed) {
          ws.close(4000, 'Heartbeat timeout')
        }
      }
    }, heartbeatInterval)
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
    if (heartbeatTimeoutTimer) {
      clearTimeout(heartbeatTimeoutTimer)
      heartbeatTimeoutTimer = null
    }
  }

  function handlePing() {
    lastPingTime = Date.now()
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'pong', ts: Date.now() }))
    }
  }

  function connect() {
    if (disposed) return
    intentionallyClosed = false
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    stopHeartbeat()
    if (connectTimeoutTimer) {
      clearTimeout(connectTimeoutTimer)
      connectTimeoutTimer = null
    }
    if (ws) {
      ws.onopen = null
      ws.onclose = null
      ws.onmessage = null
      ws.onerror = null
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, 'Reconnecting')
      }
    }

    setStatus('connecting')
    error.value = null

    connectTimeoutTimer = setTimeout(() => {
      if (status.value === 'connecting' && ws && !intentionallyClosed) {
        console.error('[WS] Connection timeout')
        error.value = 'WebSocket connection timeout'
        ws.close(4000, 'Connection timeout')
      }
    }, connectTimeout)

    try {
      ws = new WebSocket(buildUrl())
    } catch (e: any) {
      error.value = `Failed to create WebSocket: ${e.message}`
      setStatus('disconnected')
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      if (connectTimeoutTimer) {
        clearTimeout(connectTimeoutTimer)
        connectTimeoutTimer = null
      }
      reconnectAttempt.value = 0
      error.value = null
      setStatus('connected')
      console.log('[WS] Connected')
      startHeartbeat()
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'ping') {
          handlePing()
          return
        }
        lastMessage.value = data
        onMessage?.(data)
      } catch {
        onMessage?.(event.data)
      }
    }

    ws.onclose = (event) => {
      if (connectTimeoutTimer) {
        clearTimeout(connectTimeoutTimer)
        connectTimeoutTimer = null
      }
      console.log(`[WS] Closed: code=${event.code} reason=${event.reason}`)
      stopHeartbeat()
      if (event.code === 1008) {
        intentionallyClosed = true
        error.value = 'WebSocket authentication rejected; sign in again'
        setStatus('disconnected')
        return
      }
      if (!intentionallyClosed) {
        setStatus('disconnected')
        scheduleReconnect()
      }
    }

    ws.onerror = (e) => {
      console.error('[WS] Error:', e)
      error.value = 'WebSocket connection error'
    }
  }

  function send(data: string | object) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('[WS] Cannot send: not connected')
      return false
    }
    const payload = typeof data === 'string' ? data : JSON.stringify(data)
    ws.send(payload)
    return true
  }

  function close() {
    intentionallyClosed = true
    stopHeartbeat()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (connectTimeoutTimer) {
      clearTimeout(connectTimeoutTimer)
      connectTimeoutTimer = null
    }
    if (ws) {
      ws.onopen = null
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
      ws.close(1000, 'Client closing')
      ws = null
    }
    setStatus('disconnected')
    reconnectAttempt.value = 0
  }

  onUnmounted(() => {
    disposed = true
    close()
  })

  return {
    status,
    error,
    reconnectAttempt,
    lastMessage,
    connect,
    send,
    close,
  }
}
