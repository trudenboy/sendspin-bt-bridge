import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import LogsViewer from '@/components/diagnostics/LogsViewer.vue'
import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ getLogs: vi.fn(), downloadLogs: vi.fn(), setLogLevel: vi.fn() }))
const notes = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn() }))
vi.mock('@/api/diagnostics', () => api)
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notes }))
vi.mock('@/utils/clipboard', () => clipboard)

async function mountViewer() {
  setActivePinia(createPinia())
  const w = mount(LogsViewer, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
  await flushPromises()
  return w
}

describe('LogsViewer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('says the logs could not be loaded instead of "No log entries", and retries', async () => {
    api.getLogs.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({ logs: ['2025-01-01 12:00:00 INFO hello'] })
    const w = await mountViewer()
    expect(w.text()).not.toContain(en.diagnostics.logs.noLogs)
    const alert = w.get('[data-logs-error]')
    expect(alert.text()).toContain(en.diagnostics.logs.loadFailed)
    await alert.get('button').trigger('click')
    await flushPromises()
    expect(w.find('[data-logs-error]').exists()).toBe(false)
    expect(w.text()).toContain('hello')
  })

  it('still says "No log entries" when the fetch worked but is empty', async () => {
    api.getLogs.mockResolvedValue({ logs: [] })
    expect((await mountViewer()).text()).toContain(en.diagnostics.logs.noLogs)
  })

  it('confirms the applied log level, and reports a failure', async () => {
    api.getLogs.mockResolvedValue({ logs: [] })
    api.setLogLevel.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('nope'))
    const w = await mountViewer()
    await w.get('select').setValue('DEBUG')
    const apply = w.findAll('button').find((b) => b.text() === en.diagnostics.logs.apply)!
    await apply.trigger('click')
    await flushPromises()
    expect(notes.success).toHaveBeenCalledWith('Now logging at DEBUG')
    await apply.trigger('click')
    await flushPromises()
    expect(notes.error).toHaveBeenCalled()
  })
})

describe('LogsViewer line actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getLogs.mockResolvedValue({ logs: ['2025-01-01 12:00:00 ERROR boom'] })
    clipboard.copyToClipboard.mockResolvedValue(true)
  })

  it('copies the raw line from its copy button', async () => {
    const w = await mountViewer()
    await w.get('[data-copy-line]').trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith('2025-01-01 12:00:00 ERROR boom')
  })

  it('exposes the level filters as pressed toggles', async () => {
    const w = await mountViewer()
    expect(w.findAll('[aria-pressed]').every((b) => b.attributes('aria-pressed') === 'true')).toBe(true)
    await w.findAll('[aria-pressed]')[0]!.trigger('click')
    expect(w.findAll('[aria-pressed]')[0]!.attributes('aria-pressed')).toBe('false')
  })
})
