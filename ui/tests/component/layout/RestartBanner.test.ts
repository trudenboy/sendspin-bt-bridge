import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import RestartBanner from '@/components/layout/RestartBanner.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

const push = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

function mountBanner(state: string) {
  setActivePinia(createPinia())
  const bridge = useBridgeStore()
  bridge.restartState = state as never
  return { bridge, w: mount(RestartBanner, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en, ru } })] } }) }
}

describe('RestartBanner error state', () => {
  beforeEach(() => push.mockClear())

  it('can be dismissed', async () => {
    const { bridge, w } = mountBanner('error')
    await w.get('[data-restart-dismiss]').trigger('click')
    expect(bridge.restartState).toBe('idle')
  })

  it('offers "Open logs", which opens the Diagnostics logs tab', async () => {
    const { w } = mountBanner('error')
    const btn = w.get('[data-restart-logs]')
    expect(btn.text()).toBe(en.restart.openLogs)
    await btn.trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/diagnostics', query: { tab: 'logs' } })
  })

  it('announces itself as a status region', () => {
    expect(mountBanner('error').w.get('[role="status"]').exists()).toBe(true)
  })

  it('shows no buttons while the restart is in progress', () => {
    const { w } = mountBanner('stopping')
    expect(w.find('[data-restart-dismiss]').exists()).toBe(false)
    expect(w.find('[data-restart-logs]').exists()).toBe(false)
  })
})
