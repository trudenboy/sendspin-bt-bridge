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

  it('shows the name and room, with only a short technical id', () => {
    const w = mountCard(makeDevice({ name: 'Kitchen Boom @ HP-ProDesk', room: { name: 'Kitchen' } }))
    expect(w.text()).toContain('Kitchen Boom')
    expect(w.text()).not.toContain('@ HP-ProDesk')
    expect(w.text()).toContain('Kitchen')
    expect(w.text()).toContain('#EEFF')
    expect(w.text()).not.toContain('AA:BB:CC:DD:EE:FF')
  })

  it('says its state in plain words', () => {
    expect(mountCard().text()).toContain('Connected')
    expect(mountCard(streaming()).text()).toContain('Playing')
    expect(mountCard(makeDevice({ health: { state: 'degraded' } })).text()).toContain('Problem')
    expect(mountCard(makeDevice({ bluetooth: { standby: true } })).text()).toContain('Standby')
  })

  it('keeps volume and previous/next out of the summary card', () => {
    const w = mountCard(streaming())
    expect(w.find('input[type="range"]').exists()).toBe(false)
    expect(buttonLabelled(w, 'Next track')).toBeUndefined()
    expect(buttonLabelled(w, 'Previous track')).toBeUndefined()
  })

  it('offers play/pause while streaming and sends it by device id', async () => {
    const { transport } = await import('@/api/playback')
    const w = mountCard(streaming())
    await buttonLabelled(w, 'Pause')!.trigger('click')
    expect(transport).toHaveBeenCalledWith('dev-1', 'pause')
    expect(mountCard().find('button[aria-label="Play"]').exists()).toBe(true)
  })

  it('dims a disabled speaker', () => {
    expect(mountCard(makeDevice({ enabled: false })).classes()).toContain('opacity-60')
    expect(mountCard().classes()).not.toContain('opacity-60')
  })

  it.each([
    [true, 'Disable', false],
    [false, 'Enable', true],
  ])('toggles enabled (%s → %s)', async (enabled, item, next) => {
    const items = await openMenu(mountCard(makeDevice({ enabled })))
    await items.find((b) => b.text().includes(item))!.trigger('click')
    expect(store.setEnabled).toHaveBeenCalledWith('dev-1', next)
    expect(notes.undo).toHaveBeenCalledTimes(1)
  })

  it('releases and reclaims through the device store', async () => {
    let items = await openMenu(mountCard())
    await items.find((b) => b.text() === 'Release speaker')!.trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', true)
    items = await openMenu(mountCard(makeDevice({ bluetooth: { management_enabled: false } })))
    await items.find((b) => b.text() === 'Reclaim speaker')!.trigger('click')
    expect(store.release).toHaveBeenCalledWith('dev-1', false)
  })

  it('asks before removing the speaker', async () => {
    const { confirmState, settleConfirm } = await import('@/composables/useConfirm')
    let items = await openMenu(mountCard())
    await items.find((b) => b.text().includes('Remove speaker'))!.trigger('click')
    expect(confirmState.open).toBe(true)
    settleConfirm(false)
    await new Promise((r) => setTimeout(r, 0))
    expect(store.remove).not.toHaveBeenCalled()
    items = await openMenu(mountCard())
    await items.find((b) => b.text().includes('Remove speaker'))!.trigger('click')
    settleConfirm(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(store.remove).toHaveBeenCalledWith('dev-1')
  })

  it('opens the details from the title and in selection mode toggles instead', async () => {
    const open = mountCard()
    await open.findAll('button').find((b) => b.text() === 'Test Speaker')!.trigger('click')
    expect(open.emitted('openDetail')?.[0]).toEqual(['dev-1'])

    const select = mount(DeviceCard, {
      props: { device: makeDevice(), selectable: true },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    await select.findAll('button').find((b) => b.text() === 'Test Speaker')!.trigger('click')
    expect(select.emitted('toggleSelect')?.[0]).toEqual(['dev-1'])
    expect(select.emitted('openDetail')).toBeUndefined()
  })

  it('shows what is playing with a play control', () => {
    const text = mountCard(streaming()).text()
    expect(text).toContain('Song')
    expect(text).toContain('Band')
    expect(mountCard(streaming()).find('button[aria-label="Pause"]').exists()).toBe(true)
  })
})
