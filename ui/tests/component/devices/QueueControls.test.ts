import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => ({ setVolume: vi.fn(), setMute: vi.fn(), release: vi.fn(), setEnabled: vi.fn() }),
}))
vi.mock('@/stores/notifications', () => ({
  useNotificationStore: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/ma', () => ({ queueCommand: vi.fn().mockResolvedValue({}) }))

import { transport } from '@/api/playback'
import { queueCommand } from '@/api/ma'

const NATIVE = ['play', 'pause', 'next', 'previous', 'shuffle', 'unshuffle', 'repeat_off', 'repeat_one', 'repeat_all', 'seek']
const blockedQueue = {
  actions: { queue_control: { supported: true, currently_available: false, blocked_reason: 'No queue.', depends_on: [] } },
}

const playing = (playback: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
  makeDevice({
    playback: { playing: true, track: { title: 'Song', artist: 'Band', duration_ms: 200_000, progress_ms: 50_000 }, ...playback },
    audio: { streaming: true },
    capabilities: blockedQueue,
    ...extra,
  } as never)

const i18n = () => createI18n({ legacy: false, locale: 'en', messages: { en } })

async function mountCard(device: Device) {
  useBridgeStore().snapshot = makeStatus([device])
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  return mount(DeviceCard, { props: { device }, global: { plugins: [i18n(), router] } })
}

const shuffleBtn = (w: Awaited<ReturnType<typeof mountCard>>) => w.find('button[data-queue="shuffle"]')
const repeatBtn = (w: Awaited<ReturnType<typeof mountCard>>) => w.find('button[data-queue="repeat"]')

describe('queue controls on the player panel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('shows shuffle and repeat with their state when the player offers them', async () => {
    const w = await mountCard(playing({ supported_commands: NATIVE, shuffle: true, repeat_mode: 'one' }))
    expect(shuffleBtn(w).attributes('aria-pressed')).toBe('true')
    expect(shuffleBtn(w).attributes('aria-label')).toBe('Shuffle on — turn off')
    expect(repeatBtn(w).attributes('aria-pressed')).toBe('true')
    expect(repeatBtn(w).attributes('aria-label')).toBe('Repeat one — turn off')
  })

  it('hides them when neither the player nor Music Assistant offers them', async () => {
    const w = await mountCard(playing({ supported_commands: ['play', 'pause'] }))
    expect(shuffleBtn(w).exists()).toBe(false)
    expect(repeatBtn(w).exists()).toBe(false)
  })

  it('toggles shuffle and steps repeat through the native controller', async () => {
    const w = await mountCard(playing({ supported_commands: NATIVE, shuffle: false, repeat_mode: 'off' }))
    await shuffleBtn(w).trigger('click')
    await flushPromises()
    expect(transport).toHaveBeenCalledWith('dev-1', 'shuffle')
    await repeatBtn(w).trigger('click')
    await flushPromises()
    expect(transport).toHaveBeenLastCalledWith('dev-1', 'repeat_all')
  })

  it("goes through Music Assistant's queue when the player has no native command", async () => {
    const w = await mountCard(
      playing({ supported_commands: [] }, { capabilities: { actions: { queue_control: { supported: true, currently_available: true } } } }),
    )
    await shuffleBtn(w).trigger('click')
    await flushPromises()
    expect(queueCommand).toHaveBeenCalledWith('shuffle', expect.objectContaining({ device_id: 'dev-1' }), true)
  })

  it('is disabled while a command is on its way', async () => {
    let finish!: () => void
    vi.mocked(transport).mockImplementationOnce(() => new Promise<never>((resolve) => (finish = resolve as () => void)))
    const w = await mountCard(playing({ supported_commands: NATIVE }))
    await shuffleBtn(w).trigger('click')
    expect(shuffleBtn(w).attributes('disabled')).toBeDefined()
    expect(repeatBtn(w).attributes('disabled')).toBeDefined()
    finish()
    await flushPromises()
    expect(shuffleBtn(w).attributes('disabled')).toBeUndefined()
  })

  it('is disabled while Music Assistant confirms a queue command', async () => {
    const w = await mountCard(
      playing({ supported_commands: NATIVE }, { music_assistant: { now_playing: { _sync_meta: { pending_ops: [{ action: 'repeat' }] } } } }),
    )
    expect(repeatBtn(w).attributes('disabled')).toBeDefined()
    expect(repeatBtn(w).attributes('title')).toBe('Waiting for Music Assistant to confirm')
  })

  it('seeks from the progress bar when the player can seek', async () => {
    const w = await mountCard(playing({ supported_commands: NATIVE }))
    await w.get('[data-seek]').trigger('keydown', { key: 'Home' })
    await flushPromises()
    expect(transport).toHaveBeenCalledWith('dev-1', 'seek', 0)
  })

  it('keeps a plain progress bar when the player cannot seek', async () => {
    const w = await mountCard(playing({ supported_commands: ['play', 'pause'] }))
    expect(w.find('[data-seek]').exists()).toBe(false)
  })

  it('says queue controls are unavailable only when it cannot show them', async () => {
    const without = await mountCard(playing({ supported_commands: ['play', 'pause'] }))
    expect(without.text()).toContain('Queue controls unavailable')
    const native = await mountCard(playing({ supported_commands: NATIVE }))
    expect(native.text()).not.toContain('Queue controls unavailable')
  })

  it('appears in the expanded list row too', async () => {
    useBridgeStore().snapshot = makeStatus([playing({ supported_commands: NATIVE })])
    const w = mount(DeviceListRow, {
      props: { device: playing({ supported_commands: NATIVE }), expanded: true },
      attachTo: document.body,
      global: { plugins: [i18n()] },
    })
    expect(w.find('[data-row-detail] button[data-queue="shuffle"]').exists()).toBe(true)
    expect(w.find('[data-row-detail] [data-seek]').exists()).toBe(true)
    w.unmount()
  })
})
