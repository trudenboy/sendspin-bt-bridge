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

/** One audio card summary (the aggregate's shape). */
export const audioCard = (overrides: Record<string, unknown> = {}) => ({
  name: 'alsa_card.pci-0000_00_1f.3',
  driver: 'module-alsa-card',
  active_profile: 'output:analog-stereo',
  profile_count: 3,
  port_count: 3,
  endpoint_refs: [
    { kind: 'sink', name: 'alsa_output.pci-0000_00_1f.3.analog-stereo' },
    { kind: 'source', name: 'alsa_input.pci-0000_00_1f.3.analog-stereo' },
  ],
  managed_by: null,
  ...overrides,
})

/** One local output. */
export const audioSink = (overrides: Record<string, unknown> = {}) => ({
  kind: 'sink',
  name: 'alsa_output.pci-0000_00_1f.3.analog-stereo',
  description: 'Built-in audio output',
  owner: { card: 'alsa_card.pci-0000_00_1f.3', bluetooth: null, internal: null },
  state: 'running',
  sample_spec: 's16le 2ch 48000Hz',
  muted: false,
  volume: 60,
  in_use_by: null,
  active_port: 'analog-output-speaker',
  active_port_available: 'available',
  is_default: false,
  ...overrides,
})

/** One local input. */
export const audioSource = (overrides: Record<string, unknown> = {}) => ({
  kind: 'source',
  name: 'alsa_input.pci-0000_00_1f.3.analog-stereo',
  description: 'Built-in microphone',
  owner: { card: 'alsa_card.pci-0000_00_1f.3', bluetooth: null, internal: null },
  state: 'idle',
  sample_spec: 's16le 2ch 48000Hz',
  muted: null,
  volume: null,
  in_use_by: null,
  active_port: 'analog-input-internal-mic',
  active_port_available: 'unknown',
  is_default: false,
  ...overrides,
})

/** The audio half of the aggregate. */
export const audio = (overrides: Record<string, unknown> = {}) => ({
  cards: [audioCard()],
  sinks: [audioSink()],
  sources: [audioSource()],
  ...overrides,
})

/** A card detail read (the drawer's shape). */
export const audioCardDetail = (overrides: Record<string, unknown> = {}) => ({
  ...audioCard(),
  profiles: [
    { name: 'output:analog-stereo', description: 'Analog Stereo Output', available: true },
    { name: 'output:analog-stereo+input:analog-stereo', description: 'Analog Stereo Duplex', available: true },
    { name: 'off', description: 'Off', available: true },
  ],
  ports: [
    { name: 'analog-output-speaker', description: 'Speakers', available: true, active: true, profiles: [] },
    { name: 'analog-output-headphones', description: 'Headphones', available: false, active: false, profiles: [] },
    { name: 'analog-input-internal-mic', description: 'Internal microphone', available: null, active: false, profiles: [] },
  ],
  endpoints: [audioSink(), audioSource()],
  as_of: '2026-10-31T11:02:13Z',
  stale: false,
  ...overrides,
})

/** An endpoint detail read (the drawer's shape). */
export const audioEndpointDetail = (overrides: Record<string, unknown> = {}) => ({
  ...audioSink(),
  identifiers: {
    serial: 'SN1234',
    bus_path: 'pci-0000:00:1f.3',
    vendor_id: '0x8086',
    product_id: '0xa0c8',
    alsa_long_card_name: 'HDA Intel PCH',
  },
  ports: [{ name: 'analog-output-speaker', description: 'Speakers', available: true, active: true, profiles: [] }],
  monitor_of: null,
  as_of: '2026-10-31T11:02:13Z',
  stale: false,
  ...overrides,
})

/** The source feed, as the aggregate returns it; pass ``source: null`` for the off story. */
export const sourceFeed = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'waiting',
  phone_mac: null,
  capture: { kind: 'pipewire_node', node: 'bluez_input.FC_58_FA_EB_08_6C.0' },
  ma: {
    server: 'ws://ma.local:8927/sendspin',
    connected: true,
    paired: true,
    pairing_required: false,
    pairing_code: null,
    pairing_seconds_left: 0,
    error: null,
  },
  error: null,
  ...overrides,
})

/** Each read is taken a second after the previous one, as a real bridge's clock would. */
let asOfMs = Date.UTC(2026, 9, 31, 11, 2, 13)
const nextAsOf = () => new Date((asOfMs += 1000)).toISOString()

export const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: nextAsOf(),
  stale_after_s: 2,
  stale: false,
  capabilities: capabilities(),
  controllers: [controller()],
  bluetooth_devices: [btDevice()],
  audio: audio(),
  source: null,
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
