import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

const store = {
  setVolume: vi.fn(),
  setMute: vi.fn(),
  reconnect: vi.fn(),
  standby: vi.fn(),
  wake: vi.fn(),
  remove: vi.fn(),
  release: vi.fn(),
  setEnabled: vi.fn(),
}

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => store,
}))

const notes = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), undo: vi.fn() }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notes }))

vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/devices', () => ({ claimAudio: vi.fn().mockResolvedValue(undefined) }))

const streaming = () =>
  makeDevice({ playback: { playing: true, track: { title: 'Song', artist: 'Band' } }, audio: { streaming: true } })

function mountCard(device: Device = makeDevice()) {
  return mount(DeviceCard, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

function buttonLabelled(w: ReturnType<typeof mountCard>, label: string) {
  return w.findAll('button').find((b) => b.attributes('aria-label') === label)
}

async function openMenu(w: ReturnType<typeof mountCard>) {
  await buttonLabelled(w, 'More actions for Test Speaker')!.trigger('click')
  return w.findAll('button[role="menuitem"]')
}

describe('DeviceCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('names the speaker without the bridge suffix, and its room — not its MAC', () => {
    const w = mountCard(makeDevice({ name: 'Kitchen Boom @ HP-ProDesk', room: { name: 'Kitchen' } }))
    expect(w.text()).toContain('Kitchen Boom')
    expect(w.text()).not.toContain('@ HP-ProDesk')
    expect(w.text()).toContain('Kitchen')
    expect(w.text()).not.toContain('AA:BB:CC:DD:EE:FF')
  })

  it('says its state in plain words', () => {
    expect(mountCard().text()).toContain('Connected')
    expect(mountCard(streaming()).text()).toContain('Playing')
    expect(mountCard(makeDevice({ health: { state: 'degraded' } })).text()).toContain('Problem')
    expect(mountCard(makeDevice({ bluetooth: { standby: true } })).text()).toContain('Standby')
  })

  it('shows the signal as bars with its Δ dB offset, and names the tier for screen readers', () => {
    const fair = mountCard(makeDevice({ bluetooth: { rssi_dbm: -15 } }))
    expect(fair.text()).toContain('-15 Δ dB')
    expect(fair.text()).toContain('Fair signal')
    expect(fair.findAll('[data-signal-bar].is-on')).toHaveLength(2)
    const strong = mountCard(makeDevice({ bluetooth: { rssi_dbm: -5 } }))
    expect(strong.text()).toContain('Strong signal')
    expect(strong.findAll('[data-signal-bar].is-on')).toHaveLength(3)
  })

  it('shows the volume slider only while Bluetooth is connected', () => {
    expect(mountCard().find('input[type="range"]').exists()).toBe(true)
    expect(mountCard(makeDevice({ bluetooth: { connected: false } })).find('input[type="range"]').exists()).toBe(false)
  })

  describe('a local audio output', () => {
    const output = (audio: Record<string, unknown> = {}) =>
      makeDevice({
        backend: { type: 'pulse_sink', sink: 'alsa_output.usb', mac: null, adapter: null },
        bluetooth: null as never,
        health: { state: 'unknown' },
        supported_actions: ['volume', 'transport', 'power_save'],
        audio: { has_sink: true, sink_missing: false, ...audio },
      })

    it('counts as connected while its output is present', () => {
      const w = mountCard(output())
      expect(w.text()).toContain('Connected')
      expect(w.find('input[type="range"]').exists()).toBe(true)
    })

    it('says the output is missing, with no volume slider', () => {
      const w = mountCard(output({ has_sink: false, sink_missing: true }))
      expect(w.text()).toContain('Output missing')
      expect(w.find('input[type="range"]').exists()).toBe(false)
    })

    it('shows no Bluetooth details and no handed-over label', () => {
      const w = mountCard(output())
      expect(w.text()).not.toContain('Released')
      expect(w.text()).not.toMatch(/dBm|Strong|Fair|Weak/)
    })
  })

  it('shows the battery level when the speaker reports one', () => {
    expect(mountCard(makeDevice({ bluetooth: { battery_level: 42 } })).text()).toContain('42%')
  })

  it('dims a disabled speaker', () => {
    expect(mountCard(makeDevice({ enabled: false })).find('.opacity-60').exists()).toBe(true)
    expect(mountCard().find('.opacity-60').exists()).toBe(false)
  })

  it('offers transport only while streaming', () => {
    const labels = ['Pause', 'Next track', 'Previous track']
    expect(labels.filter((l) => buttonLabelled(mountCard(streaming()), l))).toHaveLength(3)
    expect(labels.filter((l) => buttonLabelled(mountCard(), l))).toHaveLength(0)
  })

  it.each([
    ['Pause', 'pause'],
    ['Next track', 'next'],
    ['Previous track', 'previous'],
  ])('sends %s to this device by id', async (label, command) => {
    const { transport } = await import('@/api/playback')
    const w = mountCard(streaming())
    await buttonLabelled(w, label)!.trigger('click')
    expect(transport).toHaveBeenCalledWith('dev-1', command)
  })

  it('hides transport the device does not support', () => {
    const device = streaming()
    device.playback.supported_commands = ['play', 'pause']
    expect(buttonLabelled(mountCard(device), 'Next track')).toBeUndefined()
  })

  it.each([
    [true, 'Disable', false],
    [false, 'Enable', true],
  ])('toggles enabled (%s → %s)', async (enabled, item, next) => {
    const items = await openMenu(mountCard(makeDevice({ enabled })))
    await items.find((b) => b.text().includes(item))!.trigger('click')
    expect(store.setEnabled).toHaveBeenCalledWith('dev-1', next)
    // Disabling and enabling are both reversible: they run at once and offer Undo.
    expect(notes.undo).toHaveBeenCalledTimes(1)
  })

  it('releases and reclaims through the device store', async () => {
    let items = await openMenu(mountCard())
    await items.find((b) => b.text() === 'Release speaker')!.trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', true)
    expect(notes.undo).toHaveBeenCalledTimes(1)

    items = await openMenu(mountCard(makeDevice({ bluetooth: { management_enabled: false } })))
    await items.find((b) => b.text() === 'Reclaim speaker')!.trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', false)
  })

  it('asks before removing the speaker', async () => {
    const { confirmState, settleConfirm } = await import('@/composables/useConfirm')
    let items = await openMenu(mountCard())
    await items.find((b) => b.text().includes('Remove speaker'))!.trigger('click')
    expect(confirmState.open).toBe(true)
    expect(confirmState.options?.danger).toBe(true)
    settleConfirm(false)
    await new Promise((r) => setTimeout(r, 0))
    expect(store.remove).not.toHaveBeenCalled()

    items = await openMenu(mountCard())
    await items.find((b) => b.text().includes('Remove speaker'))!.trigger('click')
    settleConfirm(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(store.remove).toHaveBeenCalledWith('dev-1')
  })

  it('opens the details from the card itself', async () => {
    const w = mountCard()
    await w.findAll('button').find((b) => b.text() === 'Test Speaker')!.trigger('click')
    expect(w.emitted('openDetail')?.[0]).toEqual(['dev-1'])
  })

  it('shows what is playing', () => {
    const text = mountCard(streaming()).text()
    expect(text).toContain('Song')
    expect(text).toContain('Band')
  })

  it('says how long the speaker has been on standby', () => {
    const since = new Date(Date.now() - 12 * 60_000 - 5_000).toISOString()
    expect(mountCard(makeDevice({ bluetooth: { standby: true, standby_since: since } })).text()).toContain('Standby · 12 min')
  })

  it('tells an automatic release from a hand-over', () => {
    const auto = mountCard(makeDevice({ bluetooth: { management_enabled: false, released_by: 'auto' } })).text()
    expect(auto).toContain('Released automatically')
    const user = mountCard(makeDevice({ bluetooth: { management_enabled: false, released_by: 'user' } })).text()
    expect(user).toContain('Released')
    expect(user).not.toContain('Released automatically')
  })
})
