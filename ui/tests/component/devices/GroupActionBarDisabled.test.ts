import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import GroupActionBar from '@/components/devices/GroupActionBar.vue'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import type { DisabledPlayer } from '@/stores/devices'
import { makeDevice } from '../../fixtures/device'
import { setVolume } from '@/api/playback'

const store = vi.hoisted(() => ({ setEnabled: vi.fn(), remove: vi.fn(), standby: vi.fn(), release: vi.fn(), setPowerSave: vi.fn() }))
const bridge = vi.hoisted(() => ({ refresh: vi.fn(), adapters: [] }))
const notify = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }))
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => store,
}))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notify }))
vi.mock('@/composables/useConfirm', () => confirm)
vi.mock('@/api/playback', () => ({ setVolume: vi.fn(), setMute: vi.fn(), setDevicePlayback: vi.fn() }))
vi.mock('@/api/devices', () => ({ reconnectDevice: vi.fn() }))

const off = (id: string, name: string): DisabledPlayer => ({ id, name, mac: null, adapter: null, output: false, sink: null, room: null })

function mountBar(devices: Device[], disabled: DisabledPlayer[]) {
  const ids = [...devices.map((d) => d.id), ...disabled.map((p) => p.id)]
  return mount(GroupActionBar, {
    props: {
      devices,
      total: ids.length,
      selectedCount: ids.length,
      allSelected: true,
      someSelected: false,
      selectedDevices: devices,
      selectedNames: ids,
      selectedDisabled: disabled,
    },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

const button = (w: ReturnType<typeof mountBar>, action: string) => w.find(`[data-bulk="${action}"]`)

describe('GroupActionBar with disabled players selected', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    store.setEnabled.mockResolvedValue({ enabled: true, restart_required: true })
    store.remove.mockResolvedValue(undefined)
  })

  it('offers only Enable and Remove when only disabled players are selected', () => {
    const w = mountBar([], [off('x', 'Xray'), off('y', 'Yankee')])
    expect(button(w, 'enable').exists()).toBe(true)
    expect(button(w, 'remove').exists()).toBe(true)
    for (const gone of ['mute', 'pause', 'reconnect', 'power_save', 'standby', 'release']) expect(button(w, gone).exists()).toBe(false)
    expect(w.text()).not.toContain('Group volume')
  })

  it('offers neither for running players alone', () => {
    const w = mountBar([makeDevice({ id: 'a' })], [])
    expect(button(w, 'enable').exists()).toBe(false)
    expect(button(w, 'remove').exists()).toBe(false)
  })

  it('enables the selected disabled players at once', async () => {
    const w = mountBar([makeDevice({ id: 'a' })], [off('x', 'Xray'), off('y', 'Yankee')])
    await button(w, 'enable').trigger('click')
    await flushPromises()
    expect(store.setEnabled.mock.calls).toEqual([['x', true], ['y', true]])
    expect(confirm.confirmDialog).not.toHaveBeenCalled()
    expect(notify.warning).toHaveBeenCalledWith('Restart the bridge to apply some changes')
  })

  it('removes the selected disabled players after one confirmation, naming failures', async () => {
    store.remove.mockImplementation((id: string) => (id === 'y' ? Promise.reject(new Error('no')) : Promise.resolve()))
    const w = mountBar([], [off('x', 'Xray'), off('y', 'Yankee')])
    await button(w, 'remove').trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1)
    expect(confirm.confirmDialog.mock.calls[0]![0].message).toContain('Xray, Yankee')
    expect(store.remove.mock.calls).toEqual([['x'], ['y']])
    expect(notify.error).toHaveBeenCalledWith('Remove speaker failed for: Yankee')
  })

  it('sets the group volume on running players only', async () => {
    vi.useFakeTimers()
    const w = mountBar([makeDevice({ id: 'a' })], [off('x', 'Xray')])
    w.findComponent({ name: 'SbSlider' }).vm.$emit('update:modelValue', 40)
    vi.advanceTimersByTime(400)
    vi.useRealTimers()
    await flushPromises()
    expect(vi.mocked(setVolume).mock.calls).toEqual([['a', 40]])
  })
})
