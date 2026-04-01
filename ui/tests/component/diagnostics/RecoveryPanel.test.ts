import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import RecoveryPanel from '@/components/diagnostics/RecoveryPanel.vue'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import en from '@/i18n/en.json'

vi.mock('@/api/diagnostics', () => ({
  getDiagnostics: vi.fn().mockResolvedValue({}),
  getRecoveryAssistant: vi.fn().mockResolvedValue({}),
  getOperatorGuidance: vi.fn().mockResolvedValue({}),
  downloadBugreport: vi.fn(),
  rerunChecks: vi.fn().mockResolvedValue({}),
  downloadTimelineCsv: vi.fn(),
}))

function buildI18n() {
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
}

describe('RecoveryPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows empty state when no issues', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = { issues: [] }

    const wrapper = mount(RecoveryPanel, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('All clear')
  })

  it('renders issue cards with severity badges', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = {
      issues: [
        {
          device_mac: 'AA:BB:CC:DD:EE:FF',
          issue: 'Device not responding',
          severity: 'critical',
          remediation: 'Restart the device',
        },
        {
          device_mac: '11:22:33:44:55:66',
          issue: 'Weak signal',
          severity: 'warning',
          remediation: 'Move device closer',
        },
      ],
    }

    const wrapper = mount(RecoveryPanel, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Device not responding')
    expect(wrapper.text()).toContain('critical')
    expect(wrapper.text()).toContain('Weak signal')
    expect(wrapper.text()).toContain('warning')
    expect(wrapper.text()).toContain('Restart the device')
    expect(wrapper.text()).toContain('Move device closer')
  })

  it('renders Run Checks button', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = { issues: [] }

    const wrapper = mount(RecoveryPanel, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Run Checks')
  })

  it('shows spinner when loading', () => {
    const store = useDiagnosticsStore()
    store.loading = true
    store.recovery = null

    const wrapper = mount(RecoveryPanel, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })
})
