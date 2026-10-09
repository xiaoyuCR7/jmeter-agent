// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from '../src/composables/useWebSocket'

class FakeSocket {
  static OPEN = 1
  static CONNECTING = 0
  static instances: FakeSocket[] = []
  readyState = 0
  onopen: any = null
  onclose: any = null
  onerror: any = null
  onmessage: any = null
  close = vi.fn((code: number) => {
    if (code !== 1000 && (code < 3000 || code >= 5000)) throw new Error('Invalid close code')
    this.readyState = 3
    this.onclose?.({ code, reason: '' })
  })
  constructor() { FakeSocket.instances.push(this) }
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); FakeSocket.instances = [] })

describe('WebSocket cleanup and reconnect', () => {
  it('uses an allowed application code on connection timeout', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    const channel = useWebSocket('ws://local', { connectTimeout: 20 })
    channel.connect()
    expect(() => vi.advanceTimersByTime(21)).not.toThrow()
    expect(FakeSocket.instances[0].close).toHaveBeenCalledWith(4000, 'Connection timeout')
    channel.close()
  })

  it('can reconnect explicitly after manual close without leaving stale handlers', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    const channel = useWebSocket('ws://local')
    channel.connect()
    const first = FakeSocket.instances[0]
    channel.close()
    expect(first.onopen).toBeNull()
    expect(first.onmessage).toBeNull()
    channel.connect()
    expect(FakeSocket.instances).toHaveLength(2)
    channel.close()
  })

  it('does not retry a server authentication rejection', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    const channel = useWebSocket('ws://local')
    channel.connect()
    FakeSocket.instances[0].onclose({ code: 1008, reason: 'revoked' })
    vi.advanceTimersByTime(120000)
    expect(FakeSocket.instances).toHaveLength(1)
    channel.close()
  })
})
