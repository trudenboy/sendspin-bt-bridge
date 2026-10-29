import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceSettingsPanel from '@/components/devices/DeviceSettingsPanel.vue'
import en from '@/i18n/en.json'
import { saveConfig } from '@/api/config'
import { ApiError } from '@/api/client'
import { makeDevice } from '../../fixtures/device'

const stored = () => ({
  BRIDGE_NAME: 'Bridge',
  players: [
    { id: 'cfg-y', name: 'Kitchen', backend: { type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF', adapter: 'hci0' } },
    { id: 'cfg-other', name: 'Other', backend: { type: 'bluetooth_a2dp', mac: '11:22:33:44:55:66' } },
  ],
})

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(),
  saveConfig: vi.fn(),
}))
vi.mock('@/api/devices', () => ({ setDeviceEnabled: vi.fn(), setDevicePlayoutSync: vi.fn() }))
vi.mock('@/api/audio', () => ({ getAudioSinks: vi.fn().mockResolvedValue([]) }))

async function mountPanel() {
  const { getConfig } = await import('@/api/config')
  vi.mocked(getConfig).mockResolvedValue(structuredClone(stored()) as never)
  setActivePinia(createPinia())
  const w = mount(DeviceSettingsPanel, {
    props: { device: makeDevice({ id: 'runtime-x', config_id: 'cfg-y' }) },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  await flushPromises()
  return w
}
/** The speaker's Name field (the first text field of the panel). */
const nameInput = (w: Awaited<ReturnType<typeof mountPanel>>) => w.findAll('input[type="text"]')[0]!
const names = (w: Awaited<ReturnType<typeof mountPanel>>) => w.findAll('input[type="text"]').map((i) => (i.element as HTMLInputElement).value)

describe('DeviceSettingsPanel auto-save', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    vi.mocked(saveConfig).mockReset()
    vi.mocked(saveConfig).mockResolvedValue({ warnings: [], reconfig: {} } as never)
  })
  afterEach(() => vi.useRealTimers())

  it('has no Save or Discard button', async () => {
    const w = await mountPanel()
    const labels = w.findAll('button').map((b) => b.text())
    expect(labels).not.toContain('Save')
    expect(labels).not.toContain('Discard')
  })

  it('saves a changed field by itself after a short pause, only once for quick edits, and says Saved', async () => {
    const w = await mountPanel()
    expect(names(w)[0]).toBe('Kitchen')
    await nameInput(w).setValue('Kit')
    await nameInput(w).trigger('change')
    await nameInput(w).setValue('Kitchen 2')
    await nameInput(w).trigger('change')
    expect(saveConfig).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    expect(saveConfig).toHaveBeenCalledTimes(1)
    const saved = vi.mocked(saveConfig).mock.calls[0]![0] as ReturnType<typeof stored>
    expect(saved.players[0]!.name).toBe('Kitchen 2')
    expect(saved.players[1]).toEqual(stored().players[1])
    expect(w.find('[data-save-state="saved"]').text()).toContain(en.settings.autosave.saved)
  })

  it('Undo puts the previous value back and saves it', async () => {
    const w = await mountPanel()
    await nameInput(w).setValue('Kitchen 2')
    await nameInput(w).trigger('change')
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    await w.get('[data-save-undo]').trigger('click')
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    const last = vi.mocked(saveConfig).mock.calls.at(-1)![0] as ReturnType<typeof stored>
    expect(last.players[0]!.name).toBe('Kitchen')
    expect(names(w)[0]).toBe('Kitchen')
  })

  it('rolls back and shows the server reason when saving fails', async () => {
    vi.mocked(saveConfig).mockRejectedValue(new ApiError({ status: 422, code: 'validation_error', detail: 'Name is already used' }))
    const w = await mountPanel()
    await nameInput(w).setValue('Taken')
    await nameInput(w).trigger('change')
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    const err = w.get('[data-save-state="error"]')
    expect(err.text()).toContain('Name is already used')
    expect(names(w)[0]).toBe('Kitchen')
  })

  it('saves what is pending when the panel goes away (nothing lost on close)', async () => {
    const w = await mountPanel()
    await nameInput(w).setValue('Kitchen 3')
    await nameInput(w).trigger('change')
    w.unmount()
    await flushPromises()
    expect(saveConfig).toHaveBeenCalledTimes(1)
  })
})
