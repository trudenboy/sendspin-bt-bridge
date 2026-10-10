import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaNowPlaying from '@/components/ma/MaNowPlaying.vue'
import en from '@/i18n/en.json'
import { getNowPlaying, queueCommand } from '@/api/ma'

const byGroup: Record<string, Record<string, unknown>> = {
  syncgroup_beta: { state: 'playing', track: 'Fly Me To The Moon', artist: 'Frank Sinatra' },
  syncgroup_idle: { state: 'idle', track: 'Radio Hermitage' },
}

vi.mock('@/api/ma', () => ({
  getNowPlaying: vi.fn((id?: string) => Promise.resolve(id ? byGroup[id] ?? {} : {})),
  queueCommand: vi.fn().mockResolvedValue({ job: { id: 'j', status: 'succeeded' } }),
}))
vi.mock('@/stores/jobs', () => ({ useJobsStore: () => ({ waitFor: vi.fn().mockResolvedValue({ status: 'succeeded' }) }) }))

async function mountFor(groupId: string) {
  const w = mount(MaNowPlaying, {
    props: { groupId },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  await flushPromises()
  return w
}

describe('MaNowPlaying per group', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it("shows its own group's track, not another group's", async () => {
    const beta = await mountFor('syncgroup_beta')
    expect(getNowPlaying).toHaveBeenCalledWith('syncgroup_beta')
    expect(beta.text()).toContain('Fly Me To The Moon')
    expect(beta.text()).not.toContain('Radio Hermitage')
  })

  it('marks an idle group as not playing', async () => {
    const idle = await mountFor('syncgroup_idle')
    expect(idle.text()).toContain('Radio Hermitage')
    expect(idle.text()).toContain('Not playing')
  })

  it("pauses through the group's own queue", async () => {
    const beta = await mountFor('syncgroup_beta')
    await beta.get('button[aria-label="Play / Pause"]').trigger('click')
    await flushPromises()
    expect(queueCommand).toHaveBeenCalledWith('pause', { syncgroup_id: 'syncgroup_beta' }, undefined)
  })
})
