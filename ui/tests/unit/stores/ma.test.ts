import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/ma', () => ({
  getConnection: vi.fn(),
  getGroups: vi.fn(),
  getNowPlaying: vi.fn(),
  discoverMA: vi.fn(),
  refreshGroups: vi.fn(),
  queueCommand: vi.fn(),
  signInWithPassword: vi.fn(),
  silentAuth: vi.fn(),
}))

import * as api from '@/api/ma'
import { useMaStore } from '@/stores/ma'
import type { Job } from '@/api/types'

const job = (extra: Record<string, unknown> = {}) =>
  ({ id: 'j', kind: 'k', status: 'succeeded', created_at: '', updated_at: '', progress: {}, ...extra }) as Job

describe('useMaStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('reads the connection state', async () => {
    vi.mocked(api.getConnection).mockResolvedValue({ connected: true, configured_url: 'http://ma' })
    const store = useMaStore()
    await store.fetchConnection()
    expect(store.connected).toBe(true)
  })

  it('discovers servers through a job', async () => {
    vi.mocked(api.discoverMA).mockResolvedValue(job({ result: { servers: [{ url: 'http://ma:8095' }] } }))
    const store = useMaStore()
    expect((await store.discover())[0]!.url).toBe('http://ma:8095')
    expect(store.discovering).toBe(false)
  })

  it('refreshes groups, then reads them', async () => {
    vi.mocked(api.refreshGroups).mockResolvedValue(job())
    vi.mocked(api.getGroups).mockResolvedValue([{ id: 'syncgroup_1', name: 'Kitchen', members: [] }])
    const store = useMaStore()
    await store.refresh()
    expect(store.groups[0]!.name).toBe('Kitchen')
  })

  it('shows the predicted queue state at once and follows the job', async () => {
    vi.mocked(api.queueCommand).mockResolvedValue({
      job: job(),
      op_id: 'op',
      ma_now_playing: { shuffle: true },
    } as never)
    const store = useMaStore()
    const finished = await store.queueCmd('shuffle', { syncgroup_id: 'g' }, true)
    expect(api.queueCommand).toHaveBeenCalledWith('shuffle', { syncgroup_id: 'g' }, true)
    expect(store.nowPlaying.shuffle).toBe(true)
    expect(finished.status).toBe('succeeded')
  })

  it('signs in with an MA account', async () => {
    vi.mocked(api.signInWithPassword).mockResolvedValue({ url: 'http://ma', username: 'u', message: '' })
    const store = useMaStore()
    await store.login('http://ma', 'u', 'p')
    expect(store.connected).toBe(true)
  })

  it('a refused sign-in leaves it disconnected', async () => {
    vi.mocked(api.signInWithPassword).mockRejectedValue(new Error('bad'))
    const store = useMaStore()
    await expect(store.login('http://ma', 'u', 'p')).rejects.toThrow()
    expect(store.connected).toBe(false)
  })
})
