import { describe, it, expect, vi, beforeEach } from 'vitest'

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }))

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: () => http,
}))

import { checkProxyAvailable, getBugreport, submitBugreport } from '@/api/diagnostics'
import { ApiError } from '@/api/client'

const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) })

describe('Bug report API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads the prefilled report', async () => {
    http.GET.mockResolvedValue(ok({ markdown_short: '## Report', text_full: 'Full', suggested_description: '', report: {} }))
    const result = await getBugreport()
    expect(http.GET).toHaveBeenCalledWith('/api/v1/diagnostics/bug-report')
    expect(result.markdown_short).toBe('## Report')
  })

  it('asks whether reports can go through the proxy', async () => {
    http.GET.mockResolvedValue(ok({ available: true }))
    expect((await checkProxyAvailable()).available).toBe(true)
    expect(http.GET).toHaveBeenCalledWith('/api/v1/diagnostics/bug-report/proxy')
  })

  it('submits the report body', async () => {
    http.POST.mockResolvedValue(ok({ issue_url: 'https://gh/1', issue_number: 1 }))
    const body = { title: 'Test bug', description: 'Description here', email: 't@t.co', diagnostics_text: 'diag' }
    expect((await submitBugreport(body)).issue_url).toBe('https://gh/1')
    expect(http.POST).toHaveBeenCalledWith('/api/v1/diagnostics/bug-report', { body })
  })

  it('turns a problem answer into an ApiError with its detail', async () => {
    http.POST.mockResolvedValue({
      error: { status: 429, code: 'rate_limited', detail: 'Try later' },
      response: new Response(null, { status: 429 }),
    })
    const error = await submitBugreport({ title: 'abcde', description: 'long enough', email: 'a@b.c' }).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('rate_limited')
    expect(error.message).toBe('Try later')
  })
})
