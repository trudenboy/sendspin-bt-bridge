import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import GroupActionBar from '@/components/devices/GroupActionBar.vue'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { setMute, setDevicePlayback } from '@/api/playback'
import { reconnectDevice } from '@/api/devices'

const store = vi.hoisted(() => ({
  standby: vi.fn(),
  release: vi.fn(),
  setPowerSave: vi.fn(),
}))
const notify = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }))
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => store,
}))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notify }))
vi.mock('@/composables/useConfirm', () => confirm)
vi.mock('@/api/playback', () => ({ setVolume: vi.fn(), setMute: vi.fn(), setDevicePlayback: vi.fn() }))
vi.mock('@/api/devices', () => ({ reconnectDevice: vi.fn() }))

const BT_ACTIONS = ['volume', 'transport', 'power_save', 'reconnect', 'standby', 'claim']
const speaker = (id: string, name: string, patch: Record<string, unknown> = {}) =>
  makeDevice({ id, name, supported_actions: BT_ACTIONS, ...patch } as never)
const output = (id: string, name: string) =>
  makeDevice({
    id,
    name,
    supported_actions: ['volume', 'transport', 'power_save'],
    backend: { type: 'pulse_sink', sink: 'alsa_output.x' },
    bluetooth: null,
    audio: { has_sink: true },
  } as never)

function mountBar(selected: Device[]) {
  return mount(GroupActionBar, {
    props: {
      devices: selected,
      total: selected.length,
      selectedCount: selected.length,
      allSelected: true,
      someSelected: false,
      selectedDevices: selected,
      selectedNames: selected.map((d) => d.id),
    },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

const button = (w: ReturnType<typeof mountBar>, action: string) => w.find(`[data-bulk="${action}"]`)

describe('GroupActionBar bulk power actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    store.standby.mockResolvedValue(undefined)
    store.release.mockResolvedValue(undefined)
    store.setPowerSave.mockResolvedValue(undefined)
    confirm.confirmDialog.mockResolvedValue(true)
  })

  it('offers power save, standby and release for Bluetooth speakers', () => {
    const w = mountBar([speaker('a', 'Alpha'), speaker('b', 'Bravo')])
    expect(button(w, 'power_save').exists()).toBe(true)
    expect(button(w, 'standby').exists()).toBe(true)
    expect(button(w, 'release').exists()).toBe(true)
  })

  it('offers only power save for local audio outputs', () => {
    const w = mountBar([output('o', 'Output')])
    expect(button(w, 'power_save').exists()).toBe(true)
    expect(button(w, 'standby').exists()).toBe(false)
    expect(button(w, 'release').exists()).toBe(false)
  })

  it('hides an action no selected device can take', () => {
    const w = mountBar([
      speaker('a', 'Alpha', { bluetooth: { standby: true, power_save: true } }),
      speaker('b', 'Bravo', { bluetooth: { management_enabled: false } }),
    ])
    expect(button(w, 'power_save').exists()).toBe(false)
    expect(button(w, 'standby').exists()).toBe(false)
    expect(button(w, 'release').exists()).toBe(true)
  })

  it('puts only eligible devices into power save, without asking', async () => {
    const w = mountBar([
      speaker('a', 'Alpha'),
      speaker('b', 'Bravo', { bluetooth: { power_save: true } }),
      output('o', 'Output'),
      speaker('d', 'Delta', { enabled: false }),
    ])
    await button(w, 'power_save').trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog).not.toHaveBeenCalled()
    expect(store.setPowerSave.mock.calls).toEqual([
      ['a', true],
      ['o', true],
    ])
  })

  it('asks before standby and does nothing when declined', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    const w = mountBar([speaker('a', 'Alpha'), speaker('b', 'Bravo', { bluetooth: { standby: true } })])
    await button(w, 'standby').trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1)
    expect(confirm.confirmDialog.mock.calls[0]![0].message).toContain('Alpha')
    expect(store.standby).not.toHaveBeenCalled()
  })

  it('puts the not-yet-parked speakers on standby once confirmed', async () => {
    const w = mountBar([speaker('a', 'Alpha'), speaker('b', 'Bravo', { bluetooth: { standby: true } })])
    await button(w, 'standby').trigger('click')
    await flushPromises()
    expect(store.standby.mock.calls).toEqual([['a']])
  })

  it('asks before handing speakers over, then releases each', async () => {
    const w = mountBar([speaker('a', 'Alpha'), speaker('b', 'Bravo'), output('o', 'Output')])
    await button(w, 'release').trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1)
    expect(store.release.mock.calls).toEqual([
      ['a', true],
      ['b', true],
    ])
  })

  it('names each speaker that failed', async () => {
    store.standby.mockImplementation(async (id: string) => {
      if (id === 'b') throw new Error('nope')
    })
    const w = mountBar([speaker('a', 'Alpha'), speaker('b', 'Bravo')])
    await button(w, 'standby').trigger('click')
    await flushPromises()
    expect(notify.error).toHaveBeenCalledTimes(1)
    const message = String(notify.error.mock.calls[0]![0])
    expect(message).toContain('Bravo')
    expect(message).not.toContain('Alpha')
  })
})

describe('GroupActionBar mute, pause and reconnect all', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(setMute).mockResolvedValue(undefined as never)
    vi.mocked(setDevicePlayback).mockResolvedValue(undefined as never)
    vi.mocked(reconnectDevice).mockResolvedValue(undefined as never)
  })

  it.each([
    ['mute', () => vi.mocked(setMute), 'Mute all'],
    ['pause', () => vi.mocked(setDevicePlayback), 'Pause all'],
    ['reconnect', () => vi.mocked(reconnectDevice), 'Reconnect all'],
  ] as const)('names each speaker %s failed for, and stays quiet when all succeed', async (action, api, label) => {
    const w = mountBar([speaker('a', 'Alpha'), speaker('b', 'Bravo')])
    await w.get(`[data-bulk="${action}"]`).trigger('click')
    await flushPromises()
    expect(api()).toHaveBeenCalledTimes(2)
    expect(notify.error).not.toHaveBeenCalled()

    api().mockImplementation((async (id: string) => {
      if (id === 'b') throw new Error('nope')
    }) as never)
    await w.get(`[data-bulk="${action}"]`).trigger('click')
    await flushPromises()
    expect(notify.error).toHaveBeenCalledTimes(1)
    const message = String(notify.error.mock.calls[0]![0])
    expect(message).toContain(label)
    expect(message).toContain('Bravo')
    expect(message).not.toContain('Alpha')
  })

  it('reconnects only the speakers that can reconnect', async () => {
    const w = mountBar([speaker('a', 'Alpha'), output('o', 'Output')])
    await w.get('[data-bulk="reconnect"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(reconnectDevice).mock.calls).toEqual([['a']])
    expect(notify.error).not.toHaveBeenCalled()
  })

  it('hides Reconnect all when no selected device can reconnect', () => {
    expect(mountBar([output('o', 'Output')]).find('[data-bulk="reconnect"]').exists()).toBe(false)
  })

  it('mutes only the speakers that are connected and have a sink', async () => {
    const w = mountBar([
      speaker('a', 'Alpha'),
      speaker('d', 'Delta', { enabled: false }),
      speaker('o', 'Offline', { bluetooth: { connected: false }, health: { state: 'offline' } }),
      speaker('n', 'NoSink', { audio: { has_sink: false } }),
    ])
    await w.get('[data-bulk="mute"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(setMute).mock.calls).toEqual([['a', true]])
  })

  it('pauses only connected speakers that are not parked, handed over or disabled', async () => {
    const w = mountBar([
      speaker('a', 'Alpha'),
      speaker('d', 'Delta', { enabled: false }),
      speaker('s', 'Standby', { bluetooth: { standby: true } }),
      speaker('r', 'Released', { bluetooth: { management_enabled: false } }),
      speaker('o', 'Offline', { bluetooth: { connected: false }, health: { state: 'offline' } }),
    ])
    await w.get('[data-bulk="pause"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(setDevicePlayback).mock.calls).toEqual([['a', 'pause']])
  })

  it('hides Mute all and Pause all when no selected speaker can take them', () => {
    const w = mountBar([
      speaker('s', 'Standby', { bluetooth: { standby: true }, audio: { has_sink: false } }),
      speaker('o', 'Offline', { bluetooth: { connected: false }, health: { state: 'offline' } }),
    ])
    expect(w.find('[data-bulk="mute"]').exists()).toBe(false)
    expect(w.find('[data-bulk="pause"]').exists()).toBe(false)
  })
})
