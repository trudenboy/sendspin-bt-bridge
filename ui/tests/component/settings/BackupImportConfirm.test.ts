import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import BackupSection from '@/components/settings/BackupSection.vue'
import { confirmState, settleConfirm } from '@/composables/useConfirm'
import { useConfigStore } from '@/stores/config'
import en from '@/i18n/locales/en.json'

describe('Import', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('names what is replaced and offers to download the current config first', async () => {
    const config = useConfigStore()
    const download = vi.spyOn(config, 'downloadConfig').mockImplementation(() => undefined as never)
    const upload = vi.spyOn(config, 'uploadConfig').mockResolvedValue(undefined as never)
    const w = mount(BackupSection, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
    const input = w.get('[data-testid="file-input"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['{}'], 'c.json')] })
    await input.trigger('change')
    await flushPromises()
    expect(confirmState.open).toBe(true)
    expect(confirmState.options?.consequences?.join(' ')).toMatch(/replaced/)
    confirmState.options!.link!.run()
    expect(download).toHaveBeenCalled()
    settleConfirm(false)
    await flushPromises()
    expect(upload).not.toHaveBeenCalled()
  })
})
