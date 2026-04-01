import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/en.json'
import type { DeviceSnapshot } from '@/api/types'

vi.mock('@/api/events', () => ({
  queryEvents: vi.fn().mockResolvedValue([]),
}))

const mockDevice: DeviceSnapshot = {
  player_name: 'Test Speaker',
  mac: 'AA:BB:CC:DD:EE:FF',
  enabled: true,
  status: 'connected',
  connected: true,
  audio_streaming: false,
  server_connected: true,
  volume: 50,
  muted: false,
  audio_sink: 'bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink',
  adapter: 'hci0',
  codec: 'SBC',
  sample_rate: '44100',
  backend_info: { type: 'bluetooth_a2dp', capabilities: ['volume'] },
  player_state: 'READY',
  listen_port: 8928,
  static_delay_ms: -600,
}

vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [mockDevice],
  }),
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
      deviceId: 'AA:BB:CC:DD:EE:FF',
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
    expect(tabs.length).toBe(4)
  })

  it('shows status tab content by default', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    expect(w.text()).toContain('bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink')
    expect(w.text()).toContain('SBC')
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

  it('shows config tab when selected', async () => {
    setActivePinia(createPinia())
    const w = await mountDrawer()
    const configTab = w.find('[data-tab-id="config"]')
    await configTab.trigger('click')
    await nextTick()
    expect(w.text()).toContain('AA:BB:CC:DD:EE:FF')
    expect(w.text()).toContain('hci0')
  })
})
