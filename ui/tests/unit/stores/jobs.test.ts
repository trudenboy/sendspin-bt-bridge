import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/jobs', () => ({ getJob: vi.fn() }))

import { getJob } from '@/api/jobs'
import { useJobsStore } from '@/stores/jobs'
import type { Job } from '@/api/types'

const job = (status: Job['status'], extra: Partial<Job> = {}): Job =>
  ({ id: 'j1', kind: 'bluetooth.scan', status, created_at: '', updated_at: '', progress: {}, ...extra }) as Job

describe('useJobsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('resolves at once for a job that already finished', async () => {
    const done = await useJobsStore().waitFor(job('succeeded', { result: 1 }))
    expect(done.result).toBe(1)
  })

  it('resolves when a job event reports the end', async () => {
    const store = useJobsStore()
    const waiting = store.waitFor(job('running'), 60_000)
    store.update(job('failed', { error: { code: 'pairing_failed', status: 422 } }))
    expect((await waiting).status).toBe('failed')
  })

  it('polls when no event arrives', async () => {
    vi.useFakeTimers()
    vi.mocked(getJob).mockResolvedValueOnce(job('running')).mockResolvedValueOnce(job('succeeded'))
    const waiting = useJobsStore().waitFor(job('running'), 100)
    await vi.advanceTimersByTimeAsync(250)
    expect((await waiting).status).toBe('succeeded')
    expect(getJob).toHaveBeenCalledTimes(2)
  })
})
