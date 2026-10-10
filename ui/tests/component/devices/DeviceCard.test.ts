import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

const store = {
  setVolume: vi.fn(),
  setMute: vi.fn(),
  reconnect: vi.fn(),
  standby: vi.fn(),
  wake: vi.fn(),
  forget: vi.fn(),
  release: vi.fn(),
  setEnabled: vi.fn(),
}

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => store,
}))

vi.mock('@/stores/notifications', () => ({
  useNotificationStore: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/devices', () => ({ claimAudio: vi.fn().mockResolvedValue(undefined) }))

const streaming = () => makeDevice({ playback: { playing: true }, audio: { streaming: true } })

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
  await buttonLabelled(w, 'Details')!.trigger('click')
  return w.findAll('button[role="menuitem"]')
}

describe('DeviceCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('shows the name, the speaker MAC and the adapter', () => {
    const text = mountCard().text()
    expect(text).toContain('Test Speaker')
    expect(text).toContain('AA:BB:CC:DD:EE:FF')
    expect(text).toContain('hci0')
  })

  it('derives its badge from the device health', () => {
    expect(mountCard().text()).toContain('Ready')
    expect(mountCard(streaming()).text()).toContain('Streaming')
    expect(mountCard(makeDevice({ health: { state: 'degraded' } })).text()).toContain('Degraded')
    expect(mountCard(makeDevice({ bluetooth: { standby: true } })).text()).toContain('Standby')
  })

  it('shows the volume slider only while Bluetooth is connected', () => {
    expect(mountCard().find('input[type="range"]').exists()).toBe(true)
    expect(mountCard(makeDevice({ bluetooth: { connected: false } })).find('input[type="range"]').exists()).toBe(false)
  })

  it('shows the battery level when the speaker reports one', () => {
    expect(mountCard(makeDevice({ bluetooth: { battery_level: 42 } })).text()).toContain('42%')
  })

  it('dims a disabled speaker', () => {
    expect(mountCard(makeDevice({ enabled: false })).find('.opacity-50').exists()).toBe(true)
    expect(mountCard().find('.opacity-50').exists()).toBe(false)
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
  })

  it('releases and reclaims through the device store', async () => {
    let items = await openMenu(mountCard())
    await items.find((b) => b.text() === 'Release')!.trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', true)

    items = await openMenu(mountCard(makeDevice({ bluetooth: { management_enabled: false } })))
    await items.find((b) => b.text() === 'Reclaim')!.trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', false)
  })

  it('asks before forgetting the Bluetooth bond', async () => {
    const { confirmState, settleConfirm } = await import('@/composables/useConfirm')
    let items = await openMenu(mountCard())
    await items.find((b) => b.text().includes('Forget'))!.trigger('click')
    expect(confirmState.open).toBe(true)
    expect(confirmState.options?.danger).toBe(true)
    settleConfirm(false)
    await new Promise((r) => setTimeout(r, 0))
    expect(store.forget).not.toHaveBeenCalled()

    items = await openMenu(mountCard())
    await items.find((b) => b.text().includes('Forget'))!.trigger('click')
    settleConfirm(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(store.forget).toHaveBeenCalledWith('dev-1')
  })

  it('emits openDetail with the device id', async () => {
    const w = mountCard()
    const items = await openMenu(w)
    await items.filter((b) => b.text() === 'Details').at(-1)!.trigger('click')
    expect(w.emitted('openDetail')?.[0]).toEqual(['dev-1'])
  })
})
