import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import HealthSummary from '@/components/diagnostics/HealthSummary.vue'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import en from '@/i18n/en.json'

vi.mock('@/api/diagnostics', () => ({
  getDiagnostics: vi.fn().mockResolvedValue({}),
  getRecoveryAssistant: vi.fn().mockResolvedValue({}),
  getOperatorGuidance: vi.fn().mockResolvedValue({}),
  downloadBugreport: vi.fn(),
  rerunChecks: vi.fn().mockResolvedValue({}),
}))

function buildI18n() {
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
}

describe('HealthSummary', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows spinner when loading', () => {
    const store = useDiagnosticsStore()
    store.loading = true
    store.health = null

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('renders health status card when data loaded', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = {
      status: 'healthy',
      checks: {
        audio_backend: 'ok',
        bt_controller: 'ok',
        dbus: 'ok',
        memory: 'ok',
      },
    }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Healthy')
    expect(wrapper.text()).toContain('Bridge Health')
  })

  it('renders subsystem check badges', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = {
      status: 'degraded',
      checks: {
        audio_backend: 'ok',
        bt_controller: 'warning',
        dbus: 'ok',
        memory: 'error',
      },
    }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Audio Backend')
    expect(wrapper.text()).toContain('BT Controller')
    expect(wrapper.text()).toContain('D-Bus')
    expect(wrapper.text()).toContain('Memory')
    expect(wrapper.text()).toContain('ok')
    expect(wrapper.text()).toContain('warning')
    expect(wrapper.text()).toContain('error')
  })

  it('shows degraded overall status', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = { status: 'degraded', checks: {} }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Degraded')
  })

  it('shows unknown for missing checks', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = { status: 'healthy', checks: {} }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('unknown')
  })
})
