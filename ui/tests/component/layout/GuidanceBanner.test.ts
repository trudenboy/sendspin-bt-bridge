import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import GuidanceBanner from '@/components/GuidanceBanner.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/en.json'
import { makeStatus } from '../../fixtures/status'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ query: {}, hash: '' }) }))

function mountBanner() {
  const status = makeStatus()
  ;(status.bridge as Record<string, unknown>).guidance = {
    issue_groups: [
      {
        key: 'bt',
        severity: 'warning',
        title: 'Bluetooth adapter is off',
        summary: 'Turn the adapter on so speakers can connect again.',
        primary_action: { key: 'open_bluetooth_settings', label: 'Open Bluetooth settings' },
      },
    ],
  }
  useBridgeStore().snapshot = status
  return mount(GuidanceBanner, {
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('GuidanceBanner on a phone', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('stacks the action under the text below the sm breakpoint, beside it from sm up', () => {
    const w = mountBanner()
    const body = w.get('[data-guidance-body]')
    expect(body.classes()).toEqual(expect.arrayContaining(['flex-col', 'sm:flex-row']))
    expect(body.find('[data-guidance-text]').exists()).toBe(true)
    expect(body.find('button').text()).toContain('Open Bluetooth settings')
  })
})
