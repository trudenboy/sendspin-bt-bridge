import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/sync', () => ({
  getSyncGroups: vi.fn(), getSyncGroup: vi.fn(), writeGroupDelays: vi.fn(), createSession: vi.fn(), getSession: vi.fn(),
  endSession: vi.fn(), sessionDelay: vi.fn(), listProfiles: vi.fn(), captureProfile: vi.fn(), applyProfile: vi.fn(),
  patchProfile: vi.fn(), deleteProfile: vi.fn(), sessionSignal: vi.fn(), sessionSolo: vi.fn(), sessionAb: vi.fn(),
  sessionReference: vi.fn(), sessionSkip: vi.fn(), computeProposal: vi.fn(), applyProposal: vi.fn(), undoApply: vi.fn(),
  verifySession: vi.fn(), listRestores: vi.fn(), retryRestore: vi.fn(),
}))
import * as api from '@/api/sync'
import { ApiError } from '@/api/client'
import { useSyncStore } from '@/stores/sync'

const group = { id: 'g', name: 'Downstairs', session_id: null, members: [
  { ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null } ] }

describe('sync store', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks() })
  afterEach(() => { vi.useRealTimers() })

  it('debounces live delay writes per member and goes through the session when one is open', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    await store.fetchGroup('g')
    vi.mocked(api.writeGroupDelays).mockResolvedValue(group as never)
    store.setDelay('a', 10); store.setDelay('a', 20); store.setDelay('a', 30)
    vi.advanceTimersByTime(299); expect(api.writeGroupDelays).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1); expect(api.writeGroupDelays).toHaveBeenCalledTimes(1)
    expect(api.writeGroupDelays).toHaveBeenCalledWith('g', { a: 30 })
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members: group.members } as never)
    await store.start('g', 'manual', 'device')
    vi.mocked(api.sessionDelay).mockResolvedValue({ id: 's1', state: 'measuring' } as never)
    store.setDelay('a', 40); vi.advanceTimersByTime(300)
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 40)
  })

  it('applies sync events only for its own session', async () => {
    const store = useSyncStore()
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', state: 'measuring' } as never)
    await store.start('g', 'semi', 'device')
    store.applyEvent({ id: 'other', group_id: 'g2', state: 'proposed' } as never)
    expect(store.session?.id).toBe('s1')
    store.applyEvent({ id: 's1', group_id: 'g', state: 'proposed' } as never)
    expect(store.session?.state).toBe('proposed')
  })

  it('debounces each member on its own timer', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroup).mockResolvedValue(structuredClone(group) as never)
    await store.fetchGroup('g')
    vi.mocked(api.writeGroupDelays).mockResolvedValue(group as never)
    store.setDelay('a', 10)
    vi.advanceTimersByTime(200)
    store.setDelay('b', 20)
    vi.advanceTimersByTime(100)
    expect(api.writeGroupDelays).toHaveBeenCalledTimes(1)
    expect(api.writeGroupDelays).toHaveBeenLastCalledWith('g', { a: 10 })
    vi.advanceTimersByTime(200)
    expect(api.writeGroupDelays).toHaveBeenCalledTimes(2)
    expect(api.writeGroupDelays).toHaveBeenLastCalledWith('g', { b: 20 })
  })

  it('keeps a failed live write as an error code', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroup).mockResolvedValue(structuredClone(group) as never)
    await store.fetchGroup('g')
    vi.mocked(api.writeGroupDelays).mockRejectedValue(new ApiError({ status: 403, code: 'ma_forbidden' }))
    store.setDelay('a', 10)
    await vi.advanceTimersByTimeAsync(300)
    expect(store.error).toBe('ma_forbidden')
  })

  it('adopts a running session of the shown group from an event, but not one that already ended', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroup).mockResolvedValue(structuredClone(group) as never)
    await store.fetchGroup('g')
    store.applyEvent({ id: 'old', group_id: 'g', state: 'cancelled' } as never)
    expect(store.session).toBeNull()
    store.applyEvent({ id: 'x', group_id: 'other', state: 'measuring' } as never)
    expect(store.session).toBeNull()
    store.applyEvent({ id: 's2', group_id: 'g', state: 'measuring' } as never)
    expect(store.session?.id).toBe('s2')
  })

  it('does not take a control response as the session unless it is that session', async () => {
    const store = useSyncStore()
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', state: 'measuring', signal_running: false } as never)
    await store.start('g', 'manual')
    vi.mocked(api.sessionSignal).mockResolvedValue({} as never)
    vi.mocked(api.sessionSolo).mockResolvedValue({} as never)
    vi.mocked(api.sessionAb).mockResolvedValue({} as never)
    await store.signal('start')
    await store.solo('a')
    await store.ab('a', 'b')
    expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'start')
    expect(api.sessionSolo).toHaveBeenCalledWith('s1', 'a')
    expect(api.sessionAb).toHaveBeenCalledWith('s1', 'a', 'b')
    expect(store.session).toMatchObject({ id: 's1', state: 'measuring', signal_running: false })
    vi.mocked(api.sessionSignal).mockResolvedValue({ id: 's1', group_id: 'g', state: 'measuring', signal_running: true } as never)
    await store.signal('start')
    expect(store.session?.signal_running).toBe(true)
  })

  it('forwards the remaining session controls to the open session', async () => {
    const store = useSyncStore()
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', state: 'proposed' } as never)
    await store.start('g', 'semi', 'position')
    expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'semi', point: 'position' })
    for (const fn of ['sessionReference', 'sessionSkip', 'computeProposal', 'applyProposal', 'undoApply', 'verifySession'] as const) {
      vi.mocked(api[fn]).mockResolvedValue({} as never)
    }
    await store.reference('a')
    await store.skip('b')
    await store.propose()
    await store.apply({ a: 12 }, true)
    await store.undo()
    await store.verify()
    expect(api.sessionReference).toHaveBeenCalledWith('s1', 'a')
    expect(api.sessionSkip).toHaveBeenCalledWith('s1', 'b')
    expect(api.computeProposal).toHaveBeenCalledWith('s1')
    expect(api.applyProposal).toHaveBeenCalledWith('s1', { a: 12 }, true)
    expect(api.undoApply).toHaveBeenCalledWith('s1')
    expect(api.verifySession).toHaveBeenCalledWith('s1')
    expect(store.session?.state).toBe('proposed')
  })

  it('ends the open session and forgets it', async () => {
    const store = useSyncStore()
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', state: 'measuring' } as never)
    await store.start('g', 'manual')
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    await store.end()
    expect(api.endSession).toHaveBeenCalledWith('s1')
    expect(store.session).toBeNull()
    store.applyEvent({ id: 's1', group_id: 'g', state: 'cancelled' } as never)
    expect(store.session).toBeNull()
  })

  it('picks up the session already tuning the group', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockResolvedValue({ id: 's9', group_id: 'g', state: 'measuring' } as never)
    await store.open('g')
    expect(api.getSession).toHaveBeenCalledWith('s9')
    expect(store.session?.id).toBe('s9')
  })

  it('opening another group drops the previous group session', async () => {
    const store = useSyncStore()
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', state: 'measuring' } as never)
    await store.start('g', 'manual')
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, id: 'h' } as never)
    await store.open('h')
    expect(store.session).toBeNull()
    expect(store.group?.id).toBe('h')
  })

  it('lists the groups with their read errors', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroups).mockResolvedValue([{ ...group, error: 'ma_unavailable', members: [] }] as never)
    await store.fetchGroups()
    expect(store.groups[0]?.error).toBe('ma_unavailable')
  })

  it('keeps pending restores and retries one', async () => {
    const store = useSyncStore()
    vi.mocked(api.listRestores).mockResolvedValue([
      { session_id: 'p1', group_id: 'g', error: 'ma_unavailable' },
      { session_id: 'p2', group_id: 'h', error: null },
    ] as never)
    await store.fetchRestores()
    expect(store.restoreFor('g')?.error).toBe('ma_unavailable')
    vi.mocked(api.retryRestore).mockResolvedValue({ session_id: 'p1', group_id: 'g', error: null } as never)
    await store.retryRestore('g')
    expect(api.retryRestore).toHaveBeenCalledWith('g')
    expect(store.restoreFor('g')?.error).toBeNull()
    vi.mocked(api.retryRestore).mockRejectedValue(new ApiError({ status: 404, code: 'not_found' }))
    await store.retryRestore('h')
    expect(store.restoreFor('h')).toBeUndefined()
    expect(store.restores).toHaveLength(1)
  })

  it('manages profiles of the group', async () => {
    const store = useSyncStore()
    const p = { id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: { a: 10 }, created_at: '', notes: '' }
    vi.mocked(api.getSyncGroup).mockResolvedValue(structuredClone(group) as never)
    await store.fetchGroup('g')
    vi.mocked(api.listProfiles).mockResolvedValue([p] as never)
    await store.fetchProfiles('g')
    expect(store.profiles).toHaveLength(1)
    vi.mocked(api.captureProfile).mockResolvedValue({ ...p, id: 'p2', name: 'Party', kind: 'position' } as never)
    await store.saveProfile('Party', 'position')
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Party', kind: 'position' })
    expect(store.profiles.map((x) => x.id)).toEqual(['p1', 'p2'])
    vi.mocked(api.patchProfile).mockResolvedValue({ ...p, name: 'Late' } as never)
    await store.renameProfile('p1', 'Late')
    expect(api.patchProfile).toHaveBeenCalledWith('p1', { name: 'Late' })
    expect(store.profiles[0]?.name).toBe('Late')
    vi.mocked(api.applyProfile).mockResolvedValue({ applied: { a: 10 }, skipped: [] })
    const result = await store.applyProfile('p1')
    expect(result).toEqual({ applied: { a: 10 }, skipped: [] })
    expect(store.group?.members[0]?.delay_ms).toBe(10)
    vi.mocked(api.deleteProfile).mockResolvedValue(undefined)
    await store.removeProfile('p2')
    expect(api.deleteProfile).toHaveBeenCalledWith('p2')
    expect(store.profiles.map((x) => x.id)).toEqual(['p1'])
  })
})
