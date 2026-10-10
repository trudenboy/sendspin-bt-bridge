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
    store.health = { status: 'ok' }
    store.preflight = { dbus: true, memory_mb: 2048, audio: { sinks: 2 }, bluetooth: { controller: true, daemon: 'active' } }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Healthy')
    expect(wrapper.text()).toContain('Bridge Health')
  })

  it('renders subsystem check badges', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = { status: 'degraded' }
    // Audio fine, Bluetooth daemon down, no D-Bus, low memory.
    store.preflight = { dbus: false, memory_mb: 32, audio: { sinks: 2 }, bluetooth: { controller: true, daemon: 'inactive' } }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Audio Backend')
    expect(wrapper.text()).toContain('BT Controller')
    expect(wrapper.text()).toContain('D-Bus')
    expect(wrapper.text()).toContain('Memory')
    expect(wrapper.text()).toContain('OK')
    expect(wrapper.text()).toContain('Attention')
    expect(wrapper.text()).toContain('Problem')
  })

  it('shows degraded overall status', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = { status: 'degraded' }

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Degraded')
  })

  it('shows unknown for missing checks', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.health = { status: 'ok' }
    store.preflight = null

    const wrapper = mount(HealthSummary, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Unknown')
  })
})
