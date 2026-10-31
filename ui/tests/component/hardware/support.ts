import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import HardwareView from '@/views/HardwareView.vue'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'

/** One controller as the aggregate returns it. */
export const controller = (overrides: Record<string, unknown> = {}) => ({
  id: 'hci0',
  id_synthetic: false,
  mac: 'C0:FB:F9:62:D6:9D',
  name: 'hci0',
  alias: 'Living room dongle',
  powered: true,
  present: true,
  class_of_device: '0x00010c',
  area: { id: 'living', name: 'Living room' },
  device_count: 1,
  ...overrides,
})

/** One paired device as the aggregate returns it. */
export const btDevice = (overrides: Record<string, unknown> = {}) => ({
  mac: 'FC:58:FA:EB:08:6C',
  name: 'ENEBY20',
  alias: 'ENEBY20',
  paired: true,
  bonded: true,
  trusted: true,
  connected: true,
  controller_macs: ['C0:FB:F9:62:D6:9D'],
  battery_pct: 80,
  rssi: { delta_db: -4, at: '2026-10-31T11:02:13Z' },
  transport: null,
  roles: [{ role: 'output', evidence: 'transport:active' }],
  player_id: 'eneby20',
  source_phone: false,
  ...overrides,
})

export const capabilities = (overrides: Record<string, unknown> = {}) => ({
  bluetooth: true,
  dbus: true,
  audio_server: 'pulseaudio',
  capture: false,
  default_sink: true,
  reasons: {},
  ...overrides,
})

export const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: '2026-10-31T11:02:13Z',
  stale_after_s: 2,
  stale: false,
  capabilities: capabilities(),
  controllers: [controller()],
  bluetooth_devices: [btDevice()],
  degraded: [],
  ...overrides,
})

/** The page with a real i18n instance; RouterLink is the only app plugin it needs. */
export function mountView(locale: 'en' | 'ru' = 'en') {
  return mount(HardwareView, {
    attachTo: document.body,
    global: {
      plugins: [createI18n({ legacy: false, locale, messages: { en, ru } })],
      components: {
        RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
      },
    },
  })
}
