import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceSettingsPanel from '@/components/devices/DeviceSettingsPanel.vue'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({
    players: [
      { id: 'cfg-other', name: 'Other', backend: { type: 'bluetooth_a2dp', mac: '11:22:33:44:55:66' } },
      { id: 'cfg-y', name: 'Kitchen', backend: { type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF' } },
      { id: 'cfg-p', name: 'Desk', backend: { type: 'pulse_sink', sink: 'alsa_output.gone' } },
    ],
  }),
  saveConfig: vi.fn().mockResolvedValue({ warnings: [], reconfig: {} }),
}))

vi.mock('@/api/audio', () => ({
  getAudioSinks: vi.fn().mockResolvedValue([
    { name: 'alsa_output.usb', description: 'USB DAC', in_use_by: null },
    { name: 'alsa_output.hdmi', description: 'HDMI', in_use_by: 'cfg-other' },
  ]),
}))

async function mountPanel(device: ReturnType<typeof makeDevice>) {
  setActivePinia(createPinia())
  const w = mount(DeviceSettingsPanel, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  await flushPromises()
  return w
}

describe('DeviceSettingsPanel', () => {
  it('finds a running speaker by its config id when the runtime id differs', async () => {
    const w = await mountPanel(makeDevice({ id: 'runtime-x', config_id: 'cfg-y' }))
    expect(w.text()).not.toContain(en.drawer.config.notConfigured)
  })

  it('reports a speaker without a config entry as not configured', async () => {
    const w = await mountPanel(makeDevice({ id: 'runtime-x', config_id: null }))
    expect(w.text()).toContain(en.drawer.config.notConfigured)
  })

  describe('a local audio output', () => {
    const output = () =>
      makeDevice({
        id: 'cfg-p',
        config_id: 'cfg-p',
        backend: { type: 'pulse_sink', sink: 'alsa_output.gone', mac: null, adapter: null },
        bluetooth: null as never,
      })

    it('hides the adapter and offers the output choice', async () => {
      const w = await mountPanel(output())
      expect(w.text()).not.toContain(en.settings.device.backend.adapter.label)
      expect(w.text()).toContain(en.settings.device.backend.sink.label)
    })

    it('keeps the saved output in the choice although it is gone, and lists the others', async () => {
      const w = await mountPanel(output())
      const select = w.findAll('select').find((s) => s.findAll('option').some((o) => o.text().includes('USB DAC')))!
      const values = select.findAll('option').map((o) => o.element.value)
      expect(values).toContain('alsa_output.gone')
      expect(values).toContain('alsa_output.usb')
      expect((select.element as HTMLSelectElement).value).toBe('alsa_output.gone')
    })

    it('does not offer the disconnect idle mode', async () => {
      const w = await mountPanel(output())
      const idle = w.findAll('select').find((s) => s.findAll('option').some((o) => o.element.value === 'power_save'))!
      expect(idle.findAll('option').map((o) => o.element.value)).not.toContain('auto_disconnect')
    })
  })

  it('still shows the adapter for a Bluetooth speaker', async () => {
    const w = await mountPanel(makeDevice({ id: 'runtime-x', config_id: 'cfg-y' }))
    expect(w.text()).toContain(en.settings.device.backend.adapter.label)
  })
})
