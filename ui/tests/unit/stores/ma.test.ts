import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/ma', () => ({
  getGroups: vi.fn(),
  getNowPlaying: vi.fn(),
  discoverMA: vi.fn().mockResolvedValue({ job_id: 'd1', status: 'running' }),
  queueCmd: vi.fn().mockResolvedValue({ job_id: 'q1', status: 'completed' }),
  maLogin: vi.fn(),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import { getGroups, getNowPlaying, discoverMA, queueCmd, maLogin } from '@/api/ma'
import { useMaStore } from '@/stores/ma'

describe('useMaStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useMaStore()
    expect(store.connected).toBe(false)
    expect(store.groups).toEqual([])
    expect(store.nowPlaying).toEqual({})
    expect(store.discovering).toBe(false)
  })

  it('fetchGroups loads groups', async () => {
    const mockGroups = [
      { group_id: 'g1', group_name: 'Living Room', members: [] },
    ]
    vi.mocked(getGroups).mockResolvedValue(mockGroups)
    const store = useMaStore()
    await store.fetchGroups()
    expect(store.groups).toEqual(mockGroups)
  })

  it('discover sets discovering flag', async () => {
    const store = useMaStore()
    const promise = store.discover()
    expect(store.discovering).toBe(true)
    await promise
    expect(store.discovering).toBe(false)
    expect(discoverMA).toHaveBeenCalled()
  })

  it('getNowPlaying updates cache and returns group data', async () => {
    const mockData = {
      g1: { title: 'Song', artist: 'Artist', state: 'playing' },
    }
    vi.mocked(getNowPlaying).mockResolvedValue(mockData)
    const store = useMaStore()
    const result = await store.getNowPlaying('g1')
    expect(store.nowPlaying).toEqual(mockData)
    expect(result).toEqual(mockData.g1)
  })

  it('getNowPlaying returns null for unknown group', async () => {
    vi.mocked(getNowPlaying).mockResolvedValue({})
    const store = useMaStore()
    const result = await store.getNowPlaying('missing')
    expect(result).toBeNull()
  })

  it('queueCmd calls API with correct params', async () => {
    const store = useMaStore()
    await store.queueCmd('play', 'g1', { uri: 'test' })
    expect(queueCmd).toHaveBeenCalledWith('play', 'g1', { uri: 'test' })
  })

  it('login sets connected on success', async () => {
    vi.mocked(maLogin).mockResolvedValue({ success: true, url: 'http://ma' })
    const store = useMaStore()
    const result = await store.login('ha-token-123')
    expect(store.connected).toBe(true)
    expect(result.success).toBe(true)
  })

  it('login does not set connected on failure', async () => {
    vi.mocked(maLogin).mockResolvedValue({ success: false })
    const store = useMaStore()
    await store.login('bad-token')
    expect(store.connected).toBe(false)
  })
})
