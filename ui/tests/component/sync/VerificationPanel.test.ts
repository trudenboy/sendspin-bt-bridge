import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import VerificationPanel from '@/components/sync/VerificationPanel.vue'
import { useSyncStore } from '@/stores/sync'
import type { SyncSession } from '@/api/types'
import en from '@/i18n/locales/en.json'

vi.mock('@/api/sync', () => ({ verifySession: vi.fn(), applyProposal: vi.fn() }))
import { verifySession, applyProposal } from '@/api/sync'

const session = (verification: unknown = null) => ({
  id: 's1', group_id: 'g', state: verification && (verification as { state: string }).state === 'recording' ? 'measuring' : 'tuning',
  mode: 'semi', departed: [], measurements: {}, proposal: null, verification,
  members: [{ ma_player_id: 'a', name: 'Kitchen', adjustable: true }, { ma_player_id: 'b', name: 'Hall', adjustable: true }],
}) as unknown as SyncSession

function setup(input = false) {
  const store = useSyncStore()
  store.session = session()
  const wrapper = defineComponent({
    setup: () => () => h(VerificationPanel, {
      session: store.session!, input: input ? { id: 'line', name: 'Line in', kind: 'local', available: true } : null,
    }),
  })
  return mount(wrapper, { global: {
    plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
    stubs: {
      ListeningCapture: { emits: ['next', 'cancel'], template: '<button data-complete-input @click="$emit(\'next\')">Complete input</button>' },
      PhoneCapture: { emits: ['next', 'cancel'], template: '<button data-cancel-check @click="$emit(\'cancel\')">Cancel check</button>' },
    },
  } })
}

describe('post-Apply verification panel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(verifySession).mockImplementation(async (_id, action) => action === 'start'
      ? session({ state: 'recording' })
      : session({ state: action === 'cancel' ? 'cancelled' : 'complete', spread_ms: 1.2, passed: true, heard: 2, unverified: [] }))
  })

  it('records from the bridge again and reports a measured result without another apply', async () => {
    const w = setup(true)
    await w.get('[data-verify]').trigger('click')
    await flushPromises()
    expect(verifySession).toHaveBeenCalledWith('s1', 'start')
    await w.get('[data-complete-input]').trigger('click')
    await flushPromises()
    expect(verifySession).toHaveBeenCalledWith('s1', 'finish')
    expect(w.get('[data-verification-result]').text()).toContain('Measured residual: 1.2 ms')
    expect(applyProposal).not.toHaveBeenCalled()
    w.unmount()
  })

  it('can cancel just the check and return to the applied configuration', async () => {
    const w = setup()
    await w.get('[data-verify]').trigger('click')
    await flushPromises()
    await w.get('[data-cancel-check]').trigger('click')
    await flushPromises()
    expect(verifySession).toHaveBeenCalledWith('s1', 'cancel')
    expect(w.find('[data-verify]').exists()).toBe(true)
    expect(applyProposal).not.toHaveBeenCalled()
    w.unmount()
  })

  it('a small residual from a partial check is not shown as success', async () => {
    const w = setup()
    useSyncStore().applyEvent(session({ state: 'complete', spread_ms: 1.2, passed: false, heard: 2, unverified: ['c'] }))
    await flushPromises()
    expect(w.get('[data-verification-result]').text()).toContain('incomplete')
    expect(w.get('[data-verification-result]').text()).not.toContain('Every checked speaker')
    w.unmount()
  })
})
