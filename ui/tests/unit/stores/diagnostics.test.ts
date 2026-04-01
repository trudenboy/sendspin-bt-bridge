import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/diagnostics', () => ({
  getDiagnostics: vi.fn(),
  getRecoveryAssistant: vi.fn(),
  getOperatorGuidance: vi.fn(),
  downloadBugreport: vi.fn(),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import {
  getDiagnostics,
  getRecoveryAssistant,
  getOperatorGuidance,
  downloadBugreport,
} from '@/api/diagnostics'
import { useDiagnosticsStore } from '@/stores/diagnostics'

describe('useDiagnosticsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useDiagnosticsStore()
    expect(store.health).toBeNull()
    expect(store.recovery).toBeNull()
    expect(store.guidance).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('fetchDiagnostics loads health data', async () => {
    const mockData = { status: 'ok', checks: { bt: 'pass' } }
    vi.mocked(getDiagnostics).mockResolvedValue(mockData)
    const store = useDiagnosticsStore()
    await store.fetchDiagnostics()
    expect(store.health).toEqual(mockData)
    expect(store.loading).toBe(false)
  })

  it('fetchDiagnostics sets loading during request', async () => {
    let resolve: (v: Record<string, unknown>) => void
    vi.mocked(getDiagnostics).mockReturnValue(
      new Promise((r) => {
        resolve = r
      }),
    )
    const store = useDiagnosticsStore()
    const promise = store.fetchDiagnostics()
    expect(store.loading).toBe(true)
    resolve!({ status: 'ok' })
    await promise
    expect(store.loading).toBe(false)
  })

  it('fetchRecovery loads recovery data', async () => {
    const mockData = { issues: [] }
    vi.mocked(getRecoveryAssistant).mockResolvedValue(mockData)
    const store = useDiagnosticsStore()
    await store.fetchRecovery()
    expect(store.recovery).toEqual(mockData)
  })

  it('fetchGuidance loads guidance data', async () => {
    const mockData = { phases: [{ name: 'setup', status: 'done', items: [] }] }
    vi.mocked(getOperatorGuidance).mockResolvedValue(mockData)
    const store = useDiagnosticsStore()
    await store.fetchGuidance()
    expect(store.guidance).toEqual(mockData)
  })

  it('downloadBugreport delegates to API', () => {
    const store = useDiagnosticsStore()
    store.downloadBugreport()
    expect(downloadBugreport).toHaveBeenCalled()
  })
})
