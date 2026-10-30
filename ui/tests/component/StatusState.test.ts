import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import HealthSummary from '@/components/diagnostics/HealthSummary.vue'
import StatusState from '@/components/StatusState.vue'
import { describeStatus, type StatusKind } from '@/composables/useStatusState'
import { useBridgeStore } from '@/stores/bridge'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import { makeDevice } from '../fixtures/device'
import { makeStatus } from '../fixtures/status'

vi.mock('@/api/diagnostics', () => ({
  getDiagnostics: vi.fn().mockResolvedValue({}),
  getPreflight: vi.fn().mockResolvedValue({}),
  getRecoveryAssistant: vi.fn().mockResolvedValue({}),
  getOperatorGuidance: vi.fn().mockResolvedValue({}),
  downloadBugreport: vi.fn(),
  rerunChecks: vi.fn().mockResolvedValue({}),
}))
vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({ players: [] }),
  saveConfig: vi.fn().mockResolvedValue({ warnings: [], reconfig: {} }),
}))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))
vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/devices', async (orig) => ({ ...(await orig<object>()), claimAudio: vi.fn() }))

const i18n = (locale: 'en' | 'ru' = 'en') => createI18n({ legacy: false, locale, messages: { en, ru } })

const STATES: Record<StatusKind, string[]> = {
  audio_link: ['streaming', 'connected', 'foreign', 'disconnected', 'unavailable'],
  ma_account: ['signed_in', 'signed_out', 'not_configured'],
  bluetooth: ['connected', 'connecting', 'disconnected', 'standby', 'released', 'disabled', 'not_paired'],
}

function labelsOf(kind: StatusKind, locale: 'en' | 'ru') {
  const { t, te } = i18n(locale).global
  return STATES[kind].map((state) =>
    describeStatus(kind, { state, tone: 'neutral', reason: null }, { t: (k, p) => t(k, p ?? {}), te: (k) => te(k) }).label,
  )
}

// A speaker whose Bluetooth is down: the card, the drawer and Diagnostics must all say the same.
const down = () =>
  makeDevice({
    id: 'kitchen',
    name: 'Kitchen',
    bluetooth: { connected: false },
    links: {
      bluetooth: { state: 'disconnected', tone: 'error', reason: null },
      audio_link: { state: 'unavailable', tone: 'neutral', reason: 'bluetooth_down' },
    },
  })

function seed(device = down(), bridge: Record<string, unknown> = {}) {
  const store = useBridgeStore()
  const status = makeStatus([device])
  Object.assign(status.bridge, bridge)
  store.snapshot = status
  store.loading = false
  const diagnostics = useDiagnosticsStore()
  diagnostics.loading = false
  diagnostics.health = { status: 'degraded' }
  diagnostics.preflight = null
}

const text = (w: { find: (s: string) => { text: () => string } }, sel: string) => w.find(sel).text()

describe('the three states, one rendering', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('renders the same device state with the same label in the card, the drawer and Diagnostics', async () => {
    seed()
    const device = useBridgeStore().devices[0]!
    const card = mount(DeviceCard, { props: { device }, global: { plugins: [i18n()] } })
    const drawer = mount(DeviceDetailDrawer, {
      props: { deviceId: 'kitchen', open: true },
      global: { plugins: [i18n()], stubs: { Teleport: true } },
    })
    const health = mount(HealthSummary, { global: { plugins: [i18n()] } })
    await nextTick()

    for (const kind of ['bluetooth', 'audio_link'] as const) {
      const sel = `[data-status-state="${kind}"]`
      const [a, b, c] = [text(card, sel), text(drawer, sel), text(health, sel)]
      expect(a).not.toBe('')
      expect(b).toBe(a)
      expect(c).toBe(a)
    }
    expect(text(card, '[data-status-state="bluetooth"]')).toContain('Disconnected')
    expect(text(card, '[data-status-state="audio_link"]')).toContain('Unavailable')
  })

  it('shows the Music Assistant account in the drawer and Diagnostics the same way', async () => {
    seed(down(), { music_assistant_account: { state: 'signed_out', tone: 'warning', reason: 'unreachable_or_rejected' } })
    const drawer = mount(DeviceDetailDrawer, {
      props: { deviceId: 'kitchen', open: true },
      global: { plugins: [i18n()], stubs: { Teleport: true } },
    })
    const health = mount(HealthSummary, { global: { plugins: [i18n()] } })
    await nextTick()
    const sel = '[data-status-state="ma_account"]'
    expect(text(health, sel)).toContain('Signed out')
    expect(text(drawer, sel)).toBe(text(health, sel))
  })

  it.each(['en', 'ru'] as const)('never gives the account and the audio link one label (%s)', (locale) => {
    const account = labelsOf('ma_account', locale)
    const link = labelsOf('audio_link', locale)
    expect(account.filter((l) => link.includes(l))).toEqual([])
    // Every label is a real word, not a missing-key echo.
    for (const l of [...account, ...link, ...labelsOf('bluetooth', locale)]) expect(l).not.toMatch(/^[a-z_]+$/)
  })

  it('names each state with the glossary words in both languages', () => {
    for (const [locale, names] of [
      ['en', ['Audio link', 'Music Assistant account', 'Bluetooth connection']],
      ['ru', ['Аудиоканал', 'Аккаунт Music Assistant', 'Bluetooth-соединение']],
    ] as const) {
      const { t } = i18n(locale).global
      expect([t('status.kind.audio_link'), t('status.kind.ma_account'), t('status.kind.bluetooth')]).toEqual([...names])
    }
  })

  it('keeps the audit contradiction fixed: no healthy verdict over warnings, no "connected" over a down speaker', async () => {
    seed(down())
    const health = mount(HealthSummary, { global: { plugins: [i18n()] } })
    const card = mount(DeviceCard, { props: { device: useBridgeStore().devices[0]! }, global: { plugins: [i18n()] } })
    await nextTick()
    expect(health.text()).not.toContain('Healthy')
    expect(health.text()).toContain('Degraded')
    const bt = text(card, '[data-status-state="bluetooth"]')
    expect(bt).not.toMatch(/^Connected/)
    expect(card.text()).not.toMatch(/Connected to Music Assistant/)
  })

  it('draws a state a newer bridge sends as its own code, with a neutral look', () => {
    const w = mount(StatusState, { props: { kind: 'audio_link', status: { state: 'brand_new', tone: 'odd', reason: null } }, global: { plugins: [i18n()] } })
    expect(w.text()).toContain('brand_new')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })
})
