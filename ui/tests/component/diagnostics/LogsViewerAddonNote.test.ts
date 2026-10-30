import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import LogsViewer from '@/components/diagnostics/LogsViewer.vue'
import { useAuthStore } from '@/stores/auth'
import en from '@/i18n/locales/en.json'

vi.mock('@/api/diagnostics', () => ({
  getLogs: vi.fn(async () => ({ lines: [] })),
  downloadLogs: vi.fn(),
  setLogLevel: vi.fn(),
}))

async function mountViewer() {
  const w = mount(LogsViewer, {
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  await flushPromises()
  return w
}

// #476 — the level chosen here lasts until the add-on restarts.
describe('LogsViewer add-on note', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('says the add-on option wins on restart when running as the add-on', async () => {
    useAuthStore().haAddon = true
    expect((await mountViewer()).text()).toContain(en.settings.fields.LOG_LEVEL.addonHelp)
  })

  it('says nothing about the add-on elsewhere', async () => {
    useAuthStore().haAddon = false
    expect((await mountViewer()).text()).not.toContain(en.settings.fields.LOG_LEVEL.addonHelp)
  })
})
