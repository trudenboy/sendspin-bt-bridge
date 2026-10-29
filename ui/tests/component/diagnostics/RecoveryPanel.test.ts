import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import RecoveryPanel from '@/components/diagnostics/RecoveryPanel.vue'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

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
          key: 'device_offline',
          title: 'Device not responding',
          severity: 'error',
          summary: 'Kitchen dropped its Bluetooth link.',
          primary_action: { key: 'reconnect', label: 'Restart the device' },
        },
        {
          key: 'weak_signal',
          title: 'Weak signal',
          severity: 'warning',
          summary: 'Move device closer',
        },
      ],
    }

    const wrapper = mount(RecoveryPanel, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Device not responding')
    expect(wrapper.text()).toContain('error')
    expect(wrapper.text()).toContain('Kitchen dropped its Bluetooth link.')
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

  it('wraps its toolbar so phones do not scroll horizontally', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = { issues: [] }
    const wrapper = mount(RecoveryPanel, { global: { plugins: [buildI18n()] } })
    const header = wrapper.find('h3').element.parentElement!
    expect(header.classList.contains('flex-wrap')).toBe(true)
    expect(wrapper.find('h3 + div').classes()).toContain('flex-wrap')
  })

  it('copy label follows the UI language', async () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = { issues: [{ key: 'k', severity: 'info', title: 'T' }] }
    const i18n = createI18n({ legacy: false, locale: 'en', messages: { en, ru } })
    const wrapper = mount(RecoveryPanel, { global: { plugins: [i18n] } })
    expect(wrapper.text()).toContain(en.diagnostics.copy)
    i18n.global.locale.value = 'ru'
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain(ru.diagnostics.copy)
  })
})
