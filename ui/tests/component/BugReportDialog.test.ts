import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import {
  getBugreport,
  checkProxyAvailable,
  submitBugreport,
  type BugreportData,
} from '@/api/diagnostics'

vi.mock('@/api/client', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

describe('BugReport API', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('getBugreport returns typed data', async () => {
    const { apiGet } = await import('@/api/client')
    const mockData: BugreportData = {
      markdown_short: '## Report',
      text_full: 'Full text',
      suggested_description: 'Auto issue',
      report: { env: {} },
    }
    vi.mocked(apiGet).mockResolvedValue(mockData)
    const result = await getBugreport()
    expect(result.markdown_short).toBe('## Report')
    expect(result.text_full).toBe('Full text')
  })

  it('checkProxyAvailable calls correct endpoint', async () => {
    const { apiGet } = await import('@/api/client')
    vi.mocked(apiGet).mockResolvedValue({ available: true })
    const result = await checkProxyAvailable()
    expect(result.available).toBe(true)
    expect(apiGet).toHaveBeenCalledWith('/api/bugreport/proxy-available')
  })

  it('submitBugreport sends correct payload', async () => {
    const { apiPost } = await import('@/api/client')
    vi.mocked(apiPost).mockResolvedValue({ success: true, issue_url: 'https://gh/1' })
    const result = await submitBugreport({
      title: 'Test bug',
      description: 'Description here',
      email: 'test@test.com',
      diagnostics_text: 'diag',
    })
    expect(result.success).toBe(true)
    expect(apiPost).toHaveBeenCalledWith('/api/bugreport/submit', {
      title: 'Test bug',
      description: 'Description here',
      email: 'test@test.com',
      diagnostics_text: 'diag',
    })
  })
})
