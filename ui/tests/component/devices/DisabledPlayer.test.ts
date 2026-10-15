import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import en from '@/i18n/en.json'
import DisabledPlayerCard from '@/components/devices/DisabledPlayerCard.vue'
import DisabledPlayerRow from '@/components/devices/DisabledPlayerRow.vue'
import { confirmState, settleConfirm } from '@/composables/useConfirm'
import type { DisabledPlayer } from '@/stores/devices'

const store = vi.hoisted(() => ({ setEnabled: vi.fn(), remove: vi.fn() }))
const bridge = vi.hoisted(() => ({ value: null as unknown as { adapters: unknown[]; refresh: () => unknown } }))
const notify = vi.hoisted(() => ({ error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() }))

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => store,
}))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge.value }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notify }))

const speaker = (extra: Partial<DisabledPlayer> = {}): DisabledPlayer => ({
  id: 'garage-id',
  name: 'Garage @ hp',
  mac: 'AA:00:00:00:00:03',
  adapter: 'hci1',
  output: false,
  sink: null,
  room: 'Hall',
  ...extra,
})
const output = () => speaker({ id: 'dac-1', name: 'Desk DAC', mac: null, adapter: null, output: true, sink: 'alsa_output.usb', room: null })

const i18n = () => createI18n({ legacy: false, locale: 'en', messages: { en } })

async function mountCard(player: DisabledPlayer, props: Record<string, unknown> = {}) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }, { path: '/config', name: 'config', component: { template: '<div />' } }] })
  await router.push('/')
  return mount(DisabledPlayerCard, { props: { player, ...props }, attachTo: document.body, global: { plugins: [i18n(), router] } })
}

async function openMenu(w: Awaited<ReturnType<typeof mountCard>>) {
  await w.get('button[aria-haspopup]').trigger('click')
  return w.findAll('[role="menuitem"]')
}

describe('a disabled player', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    bridge.value = reactive({ adapters: [{ id: 'hci1', mac: 'C0:00:00:00:00:01', name: 'USB dongle' }], refresh: vi.fn() })
  })

  it('shows its name, Disabled, its adapter and room, and no playback controls', async () => {
    const w = await mountCard(speaker())
    expect(w.text()).toContain('Garage')
    expect(w.text()).not.toContain('@ hp')
    expect(w.get('[data-status]').text()).toBe('Disabled')
    expect(w.get('[data-chip="adapter"]').text()).toContain('USB dongle')
    expect(w.text()).toContain('Hall')
    expect(w.find('[role="slider"]').exists()).toBe(false)
    expect(w.find('button[aria-label="Play"]').exists()).toBe(false)
    expect(w.get('article').classes()).toContain('opacity-60')
    w.unmount()
  })

  it('shows a local output by its output, with no Bluetooth', async () => {
    const w = await mountCard(output())
    expect(w.get('[data-chip="output"]').text()).toContain('alsa_output.usb')
    expect(w.find('[data-chip="adapter"]').exists()).toBe(false)
    w.unmount()
  })

  it('opens the details from its name', async () => {
    const w = await mountCard(speaker())
    await w.get('button[data-name]').trigger('click')
    expect(w.emitted('openDetail')).toEqual([['garage-id']])
    w.unmount()
  })

  it('can be selected (for Enable and Remove)', async () => {
    const w = await mountCard(speaker(), { selectable: true })
    await w.get('input[type="checkbox"]').trigger('change')
    expect(w.emitted('toggleSelect')).toEqual([['garage-id']])
    w.unmount()
  })

  it('offers Enable, Settings and Remove', async () => {
    const w = await mountCard(speaker())
    expect((await openMenu(w)).map((i) => i.text())).toEqual(['Enable', 'Settings', 'Remove speaker…'])
    w.unmount()
  })

  it('enables at once by id and says when a restart is needed', async () => {
    store.setEnabled.mockResolvedValue({ enabled: true, restart_required: true })
    const w = await mountCard(speaker())
    await (await openMenu(w))[0]!.trigger('click')
    await flushPromises()
    expect(store.setEnabled).toHaveBeenCalledWith('garage-id', true)
    expect(bridge.value.refresh).toHaveBeenCalled()
    expect(notify.warning).toHaveBeenCalledWith('Restart the bridge to apply some changes')
    w.unmount()
  })

  it('says nothing about a restart when the bridge started it at once', async () => {
    store.setEnabled.mockResolvedValue({ enabled: true, restart_required: false })
    const w = await mountCard(speaker())
    await (await openMenu(w))[0]!.trigger('click')
    await flushPromises()
    expect(store.setEnabled).toHaveBeenCalledWith('garage-id', true)
    expect(bridge.value.refresh).toHaveBeenCalled()
    expect(notify.warning).not.toHaveBeenCalled()
    expect(notify.error).not.toHaveBeenCalled()
    w.unmount()
  })

  it('opens its settings from the menu', async () => {
    const w = await mountCard(speaker())
    await (await openMenu(w))[1]!.trigger('click')
    expect(w.emitted('openDetail')).toEqual([['garage-id']])
    w.unmount()
  })

  it('is removed after confirmation', async () => {
    const w = await mountCard(speaker())
    await (await openMenu(w))[2]!.trigger('click')
    expect(confirmState.options?.title).toBe('Remove Garage?')
    settleConfirm(true)
    await flushPromises()
    expect(store.remove).toHaveBeenCalledWith('garage-id')
    w.unmount()
  })

  it('is a row in the list with the same content', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
    await router.push('/')
    const table = document.createElement('tbody')
    document.body.appendChild(table)
    const w = mount(DisabledPlayerRow, {
      props: { player: speaker(), selectable: true },
      attachTo: table,
      global: { plugins: [i18n(), router] },
    })
    expect(w.get('tr').classes()).toContain('opacity-60')
    expect(w.get('[data-status]').text()).toBe('Disabled')
    expect(w.get('[data-chip="adapter"]').text()).toContain('USB dongle')
    expect(w.find('[role="slider"]').exists()).toBe(false)
    await w.get('button[data-name]').trigger('click')
    expect(w.emitted('openDetail')).toEqual([['garage-id']])
    w.unmount()
  })
})
