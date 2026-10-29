import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

const api = vi.hoisted(() => ({ repairDevice: vi.fn().mockResolvedValue(undefined), reconnectDevice: vi.fn(), openPairingWindow: vi.fn(), setPowerSave: vi.fn(), unmuteSink: vi.fn() }))
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/api/devices', async (orig) => ({ ...(await orig<object>()), ...api }))
vi.mock('@/composables/useConfirm', () => confirm)
const state = vi.hoisted(() => ({ device: null as unknown }))
const dev = makeDevice({
  id: 'dev-1',
  music_assistant: { syncgroup_id: 'g1' },
  recent_events: [
    { at: '2026-10-30T10:00:00Z', event_type: 'a', message: 'one', level: 'info' },
    { at: '2026-10-30T10:01:00Z', event_type: 'b', message: 'two', level: 'info' },
    { at: '2026-10-30T10:02:00Z', event_type: 'c', message: 'three', level: 'info' },
    { at: '2026-10-30T10:03:00Z', event_type: 'd', message: 'four', level: 'info' },
    { at: '2026-10-30T10:04:00Z', event_type: 'e', message: 'five', level: 'info' },
  ] as never,
})
state.device = dev

vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [dev],
    adapters: [{ id: 'hci0', mac: 'AA', name: 'Living room', powered: true }],
    maConnected: false,
    maAccount: null,
    deviceById: (id: string) => (id === dev.id ? dev : undefined),
    refreshAdapters: vi.fn(),
  }),
}))
vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({ players: [] }),
  saveConfig: vi.fn(),
}))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(), createSession: vi.fn(), uploadRecording: vi.fn(), endSession: vi.fn(),
}))

async function open() {
  setActivePinia(createPinia())
  const w = mount(DeviceDetailDrawer, {
    props: { deviceId: 'dev-1', open: true },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { Teleport: true, RouterLink: { template: '<a><slot /></a>' } } },
  })
  await flushPromises()
  return w
}

describe('device drawer structure', () => {
  beforeEach(() => vi.clearAllMocks())

  it('has the four tabs Overview, Settings, Timing, History', async () => {
    const w = await open()
    expect(w.findAll('[role="tab"]').map((t) => t.text())).toEqual(['Overview', 'Settings', 'Timing', 'History'])
  })

  it('the tab bar fits a 375 px phone: tabs share the width and the bar does not scroll sideways', async () => {
    const w = await open()
    const bar = w.get('[role="tablist"]')
    expect(bar.classes()).not.toContain('overflow-x-auto')
    for (const tab of w.findAll('[role="tab"]')) expect(tab.classes()).toContain('flex-1')
  })

  it('Overview merges the audio path and lists only the latest events, linking to History', async () => {
    const w = await open()
    const overview = w.get('[data-overview]')
    expect(overview.find('[data-audio-path]').exists()).toBe(true)
    expect(overview.findAll('[data-overview-event]').length).toBe(3)
    await overview.get('[data-all-events]').trigger('click')
    expect(w.get('[data-tab-id="history"]').attributes('aria-selected')).toBe('true')
  })

  it('has exactly one primary button on Overview', async () => {
    const w = await open()
    const primary = w.get('[data-overview]').findAll('[data-primary-action]')
    expect(primary.length).toBe(1)
  })

  it('keeps recovery tools in a menu labelled Troubleshoot', async () => {
    const w = await open()
    const trigger = w.findAll('button').find((b) => b.text() === en.drawer.troubleshoot.label)!
    expect(trigger).toBeTruthy()
    await trigger.trigger('click')
    const items = w.findAll('[role="menuitem"]').map((i) => i.text())
    expect(items).toEqual(expect.arrayContaining([en.drawer.actions.repair, en.drawer.actions.unmute]))
  })

  it('moved the adapter power and reboot tools out of the drawer', async () => {
    const w = await open()
    expect(w.find('[data-adapter-power]').exists()).toBe(false)
    expect(w.find('[data-adapter-reboot]').exists()).toBe(false)
  })

  it('Timing shows the single delay value and the group link, with the details folded', async () => {
    const w = await open()
    await w.get('[data-tab-id="timing"]').trigger('click')
    const timing = w.get('[data-timing-main]')
    expect(timing.text()).toContain(en.sync.tuneLink)
    const folded = w.get('details[data-timing-details]')
    expect(folded.attributes('open')).toBeUndefined()
  })

  it('Pair again asks first and names what happens; declining does nothing', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    const w = await open()
    await w.findAll('button').find((b) => b.text() === en.drawer.troubleshoot.label)!.trigger('click')
    await w.findAll('[role="menuitem"]').find((i) => i.text() === en.drawer.actions.repair)!.trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1)
    expect(confirm.confirmDialog.mock.calls[0]![0].consequences.join(' ')).toContain('Test Speaker')
    expect(api.repairDevice).not.toHaveBeenCalled()
  })

  it('Pair again goes ahead once confirmed', async () => {
    confirm.confirmDialog.mockResolvedValue(true)
    const w = await open()
    await w.findAll('button').find((b) => b.text() === en.drawer.troubleshoot.label)!.trigger('click')
    await w.findAll('[role="menuitem"]').find((i) => i.text() === en.drawer.actions.repair)!.trigger('click')
    await flushPromises()
    expect(api.repairDevice).toHaveBeenCalledWith('dev-1')
  })
})
