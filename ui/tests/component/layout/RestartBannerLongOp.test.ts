import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import RestartBanner from '@/components/layout/RestartBanner.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/en.json'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
import { vi } from 'vitest'

function banner(state: string, reason: 'restart' | 'update') {
  setActivePinia(createPinia())
  const bridge = useBridgeStore()
  bridge.restartState = state as never
  bridge.restartReason = reason
  bridge.restartStartedAt = Date.now()
  return mount(RestartBanner, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
}

describe('RestartBanner as a long operation', () => {
  it('says the bridge is updating, not just restarting, and counts the seconds', () => {
    const w = banner('stopping', 'update')
    expect(w.get('[data-testid="longop"]').text()).toContain(en.restart.updating)
    expect(w.find('[data-testid="longop-elapsed"]').exists()).toBe(true)
  })

  it('a plain restart keeps its wording', () => {
    expect(banner('restarting', 'restart').text()).toContain(en.restart.restarting)
  })

  it('after an update it says the bridge is updated', () => {
    expect(banner('ready', 'update').text()).toContain(en.restart.updated)
  })

  it('a failed update gives the next step', () => {
    const w = banner('error', 'update')
    expect(w.text()).toContain(en.restart.updateFailed)
    expect(w.text()).toContain(en.restart.errorNext)
  })
})
