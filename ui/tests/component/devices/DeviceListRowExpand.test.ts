import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

const store = vi.hoisted(() => ({
  setVolume: vi.fn(),
  setMute: vi.fn(),
  setEnabled: vi.fn(),
  reconnect: vi.fn(),
  standby: vi.fn(),
  wake: vi.fn(),
  release: vi.fn(),
}))

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => store,
}))
const notes = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), undo: vi.fn() }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notes }))
vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))

import { transport } from '@/api/playback'

const BT_ACTIONS = ['volume', 'transport', 'power_save', 'reconnect', 'standby', 'claim']

function mountRow(device: Device, expanded = false) {
  return mount(DeviceListRow, {
    props: { device, expanded, selectable: true },
    attachTo: document.body,
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

const playing = (extra: Record<string, unknown> = {}) =>
  makeDevice({
    supported_actions: BT_ACTIONS,
    playback: { playing: true, track: { title: 'Song', artist: 'Band' } },
    audio: { streaming: true },
    timing: { available: true },
    music_assistant: { now_playing: { track: 'Song', artist: 'Band', image_url: 'api/v1/ma/artwork?sig=1' } },
    transfer_readiness: { ready: false, reason: 'no_room', severity: 'warning' },
    capabilities: {
      actions: { queue_control: { supported: true, currently_available: false, blocked_reason: 'No queue.', depends_on: [] } },
    },
    ...extra,
  } as never)

const output = () =>
  makeDevice({
    supported_actions: ['volume', 'transport', 'power_save'],
    backend: { type: 'pulse_sink', sink: 'alsa_output.usb', mac: null, adapter: null },
    bluetooth: null as never,
    health: { state: 'unknown' },
    audio: { sink_name: 'alsa_output.usb', has_sink: true, sink_missing: false },
  } as never)

describe('DeviceListRow expansion', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    store.reconnect.mockResolvedValue(undefined)
    store.standby.mockResolvedValue(undefined)
    store.release.mockResolvedValue(undefined)
  })

  it('has an expand button that reports its state', async () => {
    const w = mountRow(playing())
    const toggle = w.get('[data-row-toggle]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(w.emitted('toggleExpand')).toEqual([['dev-1']])
    await w.setProps({ expanded: true })
    expect(w.get('[data-row-toggle]').attributes('aria-expanded')).toBe('true')
    expect(w.get('[data-row-toggle]').attributes('aria-controls')).toBe(w.get('[data-row-detail]').attributes('id'))
  })

  it('expands when the row itself is clicked, not its controls', async () => {
    const w = mountRow(playing())
    await w.get('tr').trigger('click')
    expect(w.emitted('toggleExpand')).toHaveLength(1)
    await w.find('input[type="range"]').trigger('click')
    await w.find('input[type="checkbox"]').trigger('click')
    const name = w.findAll('button').find((b) => b.text() === 'Test Speaker')!
    await name.trigger('click')
    expect(w.emitted('toggleExpand')).toHaveLength(1)
    expect(w.emitted('openDetail')).toEqual([['dev-1']])
  })

  it('shows nothing extra while collapsed', () => {
    expect(mountRow(playing()).find('[data-row-detail]').exists()).toBe(false)
  })

  it('shows the card content when expanded: now playing, artwork, chips, hints', () => {
    const w = mountRow(playing(), true)
    const detail = w.get('[data-row-detail]')
    expect(detail.text()).toContain('Song')
    expect(detail.text()).toContain('Band')
    expect(detail.find('img').exists()).toBe(true)
    expect(detail.find('[data-chip="sync"]').exists()).toBe(true)
    expect(detail.find('[data-chip="transfer"]').exists()).toBe(true)
    expect(detail.text()).toContain('No queue.')
    expect(detail.find('[aria-label="Next track"]').exists()).toBe(true)
  })

  it('runs transport from the expanded block', async () => {
    const w = mountRow(playing(), true)
    await w.get('[data-row-detail]').get('[aria-label="Next track"]').trigger('click')
    expect(transport).toHaveBeenCalledWith('dev-1', 'next')
  })

  it('offers quick actions for a Bluetooth speaker', async () => {
    const w = mountRow(playing(), true)
    await w.get('[data-quick="reconnect"]').trigger('click')
    await w.get('[data-quick="standby"]').trigger('click')
    await w.get('[data-quick="release"]').trigger('click')
    await flushPromises()
    expect(store.reconnect).toHaveBeenCalledWith('dev-1')
    expect(store.standby).toHaveBeenCalledWith('dev-1')
    expect(store.release).toHaveBeenCalledWith('dev-1', true)
    expect(notes.undo).toHaveBeenCalledTimes(2) // standby and release both offer Undo
  })

  it('offers wake and take back where they apply', async () => {
    const parked = mountRow(playing({ bluetooth: { standby: true } }), true)
    await parked.get('[data-quick="wake"]').trigger('click')
    expect(store.wake).toHaveBeenCalledWith('dev-1')
    const released = mountRow(playing({ bluetooth: { management_enabled: false } }), true)
    expect(released.find('[data-quick="reconnect"]').exists()).toBe(false)
    await released.get('[data-quick="reclaim"]').trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', false)
  })

  it('shows no Bluetooth quick actions for a local audio output', () => {
    const w = mountRow(output(), true)
    expect(w.find('[data-row-detail]').exists()).toBe(true)
    expect(w.find('[data-quick]').exists()).toBe(false)
  })

  it('cannot expand a disabled speaker', () => {
    expect(mountRow(makeDevice({ enabled: false })).find('[data-row-toggle]').exists()).toBe(false)
  })

  it('offers no quick actions for a disabled speaker', () => {
    const w = mountRow(playing({ enabled: false, bluetooth: { management_enabled: false } }), true)
    expect(w.find('[data-quick]').exists()).toBe(false)
  })
})
