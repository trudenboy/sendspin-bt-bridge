import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import SystemPanel from '@/components/diagnostics/SystemPanel.vue'
import en from '@/i18n/en.json'
import { addHook, removeHook } from '@/api/system'

vi.mock('@/api/system', () => ({
  getTelemetry: vi.fn().mockResolvedValue({
    bridge: { uptime_seconds: 7260, process_rss_mb: 133.4, arch: 'x86_64', kernel: '6.8', bluez: 'bluetoothctl: 5.72' },
    subprocesses: [{ name: 'Kitchen', pid: 42, alive: true, zombie_restarts: 2, process_rss_mb: 80 }],
  }),
  listHooks: vi.fn().mockResolvedValue([
    { id: 'h1', url: 'http://ha.local/hook', categories: [], event_types: [], success_count: 3, failure_count: 1, last_error: null },
  ]),
  addHook: vi.fn().mockResolvedValue({}),
  removeHook: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/api/diagnostics', () => ({ downloadBugreport: vi.fn(), downloadLogs: vi.fn(), downloadTimelineCsv: vi.fn() }))
vi.mock('@/api/config', () => ({ downloadConfig: vi.fn() }))

async function mountPanel() {
  const w = mount(SystemPanel, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
  await flushPromises()
  return w
}

describe('SystemPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('shows bridge facts and player processes', async () => {
    const w = await mountPanel()
    expect(w.text()).toContain('2h 1m')
    expect(w.text()).toContain('133 MB')
    expect(w.text()).toContain('5.72')
    const row = w.findAll('tbody tr')[0]!
    expect(row.text()).toContain('Kitchen')
    expect(row.text()).toContain('42')
    expect(row.text()).toContain('Running')
  })

  it('adds a webhook with the categories split', async () => {
    const w = await mountPanel()
    const [url, cats] = w.findAll('form input')
    await url!.setValue('http://ha.local/new')
    await cats!.setValue('device, bridge')
    await w.get('form').trigger('submit')
    await flushPromises()
    expect(addHook).toHaveBeenCalledWith('http://ha.local/new', ['device', 'bridge'])
  })

  it('removes a webhook', async () => {
    const w = await mountPanel()
    expect(w.text()).toContain('3 delivered, 1 failed')
    await w.get('button[title="Remove"]').trigger('click')
    await flushPromises()
    expect(removeHook).toHaveBeenCalledWith('h1')
  })
})
