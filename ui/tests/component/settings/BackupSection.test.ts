import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import BackupSection from '@/components/settings/BackupSection.vue'
import en from '@/i18n/locales/en.json'

const store = vi.hoisted(() => ({ uploadConfig: vi.fn(), downloadConfig: vi.fn(), config: {} }))
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
const notes = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@/stores/config', () => ({ useConfigStore: () => store }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notes }))
vi.mock('@/composables/useConfirm', () => confirm)

async function pick() {
  const w = mount(BackupSection, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
  const input = w.get('[data-testid="file-input"]')
  Object.defineProperty(input.element, 'files', { value: [new File(['{}'], 'c.json')], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

describe('BackupSection import', () => {
  beforeEach(() => vi.clearAllMocks())

  it('asks before replacing the whole configuration, and does nothing when declined', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    await pick()
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1)
    expect(confirm.confirmDialog.mock.calls[0]![0].danger).toBe(true)
    expect(store.uploadConfig).not.toHaveBeenCalled()
  })

  it('imports once confirmed', async () => {
    confirm.confirmDialog.mockResolvedValue(true)
    await pick()
    expect(store.uploadConfig).toHaveBeenCalledTimes(1)
  })
})
