import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

const mockDevice = makeDevice({ bluetooth: { codec_name: 'SBC' }, audio: { static_delay_ms: 120 } })
const DEVICE_ID = mockDevice.id

vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [mockDevice],
    adapters: [{ id: 'hci0', mac: 'C0:FB:F9:62:D6:9D', name: 'Living room', powered: true }],
    maConnected: false,
    deviceById: (id: string) => (id === mockDevice.id ? mockDevice : undefined),
    refreshAdapters: vi.fn(),
  }),
}))

vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({ players: [] }),
  saveConfig: vi.fn().mockResolvedValue({ warnings: [], reconfig: {} }),
}))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))

function buildI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
}

async function mountDrawer(props: Record<string, unknown> = {}) {
  const w = mount(DeviceDetailDrawer, {
    props: {
      deviceId: 'dev-1',
      open: true,
      ...props,
    },
    global: {
      plugins: [buildI18n()],
      stubs: { Teleport: true },
    },
  })
  await nextTick()
  await nextTick()
  return w
}

describe('DeviceDetailDrawer', () => {
  afterEach(() => {
    document.body.classList.remove('overflow-hidden')
  })

  it('renders drawer when open', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    expect(w.find('[role="dialog"]').exists()).toBe(true)
  })

  it('does not render when closed', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer({ open: false })
    expect(w.find('[role="dialog"]').exists()).toBe(false)
  })

  it('shows device name as drawer title', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    expect(w.text()).toContain('Test Speaker')
  })

  it('shows tabs', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    expect(w.find('[role="tablist"]').exists()).toBe(true)
    const tabs = w.findAll('[role="tab"]')
    expect(tabs.map((t) => t.text())).toEqual(['Status', 'Settings', 'Timing', 'Events', 'Signal Path'])
  })

  it('shows status tab content by default', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    expect(w.text()).toContain('bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink')
    expect(w.text()).toContain('SBC')
  })

  it('shows the delay the speaker reports only when it reports one', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    expect(w.find('[data-reported-delay]').exists()).toBe(false)

    mockDevice.bluetooth!.reported_delay_ms = 123.4
    try {
      const reporting = await mountDrawer()
      const row = reporting.find('[data-reported-delay]')
      expect(row.exists()).toBe(true)
      expect(row.text()).toContain('Reported delay')
      expect(row.text()).toContain('123.4 ms')
    } finally {
      mockDevice.bluetooth!.reported_delay_ms = null
    }
  })

  it('offers repair and the pairing window only when the backend supports them', async () => {
    setActivePinia(createPinia())
    const all = mockDevice.supported_actions
    const open = async () => {
      const w = await mountDrawer()
      const more = w.findAll('button').find((b) => b.attributes('title') === 'More actions')!
      await more.trigger('click')
      return w.findAll('[role="menuitem"]').map((i) => i.text())
    }
    try {
      expect(await open()).toEqual(expect.arrayContaining(['Reconnect', 'Pair again', 'Let Music Assistant pair']))
      mockDevice.supported_actions = ['volume', 'transport', 'power_save']
      const items = await open()
      expect(items).not.toContain('Reconnect')
      expect(items).not.toContain('Pair again')
      expect(items).not.toContain('Let Music Assistant pair')
    } finally {
      mockDevice.supported_actions = all
    }
  })

  it('emits update:open on close', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    const closeBtn = w.find('[data-testid="drawer-close-btn"]')
    if (closeBtn.exists()) {
      await closeBtn.trigger('click')
      expect(w.emitted('update:open')?.[0]).toEqual([false])
    }
  })

  it('shows the speaker settings when selected', async () => {
    setActivePinia(createPinia())
    const api = await import('@/api/config')
    vi.spyOn(api, 'getConfig').mockResolvedValue({
      players: [
        { id: DEVICE_ID, name: 'Test Speaker', backend: { type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF', adapter: 'hci0' } },
      ],
    } as never)
    const w = await mountDrawer()
    await w.find('[data-tab-id="settings"]').trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    expect(w.text()).toContain('AA:BB:CC:DD:EE:FF')
    const adapter = w.findAll('select').find((sel) => sel.findAll('option').some((o) => o.text().startsWith('hci0')))!
    expect((adapter.element as HTMLSelectElement).value).toBe('hci0')
  })
})

describe('DeviceDetailDrawer config save', () => {
  it('saves the whole configuration with only this speaker changed', async () => {
    setActivePinia(createPinia())
    const api = await import('@/api/config')
    const stored = {
      BRIDGE_NAME: 'Bridge',
      MA_API_URL: 'http://ma:8095',
      players: [
        {
          id: DEVICE_ID,
          name: 'Test Speaker',
          enabled: true,
          backend: { type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF', adapter: 'hci0' },
          _effective_listen_port: 8928,
        },
        { id: 'other-id', name: 'Other', enabled: true, backend: { type: 'bluetooth_a2dp', mac: '11:22:33:44:55:66', adapter: '' } },
      ],
    }
    const getConfig = vi.spyOn(api, 'getConfig').mockResolvedValue(structuredClone(stored) as never)
    const saveConfig = vi.spyOn(api, 'saveConfig').mockResolvedValue({ warnings: [], reconfig: {} } as never)
    const w = await mountDrawer()
    await w.find('[data-tab-id="settings"]').trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    const name = w.findAll('input[type="text"]').find((i) => (i.element as HTMLInputElement).value === 'Test Speaker')!
    await name.setValue('Kitchen')
    await name.trigger('change')
    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await new Promise((r) => setTimeout(r, 0))

    expect(getConfig).toHaveBeenCalled()
    const saved = saveConfig.mock.calls[0]![0] as typeof stored
    expect(saved.BRIDGE_NAME).toBe('Bridge')
    expect(saved.MA_API_URL).toBe('http://ma:8095')
    expect(saved.players[0]!.name).toBe('Kitchen')
    expect(saved.players[0]!.backend).toEqual({ type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF', adapter: 'hci0' })
    expect(saved.players[0]).not.toHaveProperty('_effective_listen_port')
    expect(saved.players[1]).toEqual(stored.players[1])
  })

  it('writes the adapter into backend, not a dotted top-level key', async () => {
    setActivePinia(createPinia())
    const api = await import('@/api/config')
    const stored = {
      players: [
        { id: DEVICE_ID, name: 'Test Speaker', enabled: true, backend: { type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF', adapter: 'hci0' } },
      ],
    }
    vi.spyOn(api, 'getConfig').mockResolvedValue(structuredClone(stored) as never)
    const saveConfig = vi.spyOn(api, 'saveConfig').mockResolvedValue({ warnings: [], reconfig: {} } as never)
    const w = await mountDrawer()
    await w.find('[data-tab-id="settings"]').trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    const adapter = w.findAll('select').find((sel) => sel.findAll('option').some((o) => o.text().startsWith('hci0')))!
    await adapter.setValue('')
    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    const saved = saveConfig.mock.calls.at(-1)![0] as typeof stored
    expect(Object.keys(saved.players[0]!)).not.toContain('backend.adapter')
    expect(saved.players[0]!.backend).toEqual({ type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF' })
  })
})
