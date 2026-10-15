import { describe, it, expect, vi, afterEach } from 'vitest'
import { setCsrfToken } from '@/api/client'
import { endSessionOnPageHide, uploadRecording } from '@/api/sync'

function stubFetch() {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ member: 'a', status: 'ok' }), { status: 200, headers: { 'content-type': 'application/json' } }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const headersOf = (fetchMock: ReturnType<typeof vi.fn>) => (fetchMock.mock.calls[0]![1] as RequestInit).headers as Record<string, string>

describe('uploadRecording', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    setCsrfToken('')
  })

  it('posts raw PCM with the session CSRF token', async () => {
    const fetchMock = stubFetch()
    setCsrfToken('tok')
    await uploadRecording('s1', 'a', 1.5, 16000, new Int16Array([1, 2, 3]))
    const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit]
    expect(url).toContain('/api/v1/sync/sessions/s1/recordings?member=a&start_time_s=1.5&sample_rate=16000')
    expect(init.method).toBe('POST')
    expect(headersOf(fetchMock)['X-CSRF-Token']).toBe('tok')
    expect((init.body as ArrayBuffer).byteLength).toBe(6)
  })

  it('sends no CSRF header when there is no token (auth off)', async () => {
    const fetchMock = stubFetch()
    await uploadRecording('s1', 'a', 0, 16000, new Int16Array([1]))
    expect(headersOf(fetchMock)).not.toHaveProperty('X-CSRF-Token')
    expect(headersOf(fetchMock)['content-type']).toBe('application/octet-stream')
  })
})

describe('endSessionOnPageHide', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    setCsrfToken('')
  })

  it('ends the session with a keepalive DELETE that carries the CSRF token', () => {
    const fetchMock = stubFetch()
    setCsrfToken('tok')
    endSessionOnPageHide('s 1')
    const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit]
    expect(url).toMatch(/\/api\/v1\/sync\/sessions\/s%201$/)
    expect(init.method).toBe('DELETE')
    expect(init.keepalive).toBe(true)
    expect(headersOf(fetchMock)['X-CSRF-Token']).toBe('tok')
  })

  it('never throws, even when fetch does', () => {
    vi.stubGlobal('fetch', vi.fn(() => { throw new Error('gone') }))
    expect(() => endSessionOnPageHide('s1')).not.toThrow()
  })
})
