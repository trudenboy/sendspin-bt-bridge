import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import BugReportDialog from '@/components/BugReportDialog.vue'
import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  getBugreport: vi.fn(), checkProxyAvailable: vi.fn(), submitBugreport: vi.fn(), downloadBugreport: vi.fn(),
}))
vi.mock('@/api/diagnostics', () => api)

async function open() {
  setActivePinia(createPinia())
  const w = mount(BugReportDialog, {
    props: { modelValue: false },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { Teleport: true } },
  })
  await w.setProps({ modelValue: true })
  await flushPromises()
  return w
}

describe('BugReportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getBugreport.mockResolvedValue({ markdown_short: 'm', text_full: 't', suggested_description: '' })
    api.checkProxyAvailable.mockResolvedValue({ available: true })
  })

  it('shows the minimum lengths as hints before anything is typed', async () => {
    const w = await open()
    expect(w.text()).toContain('At least 5 characters')
    expect(w.text()).toContain('At least 10 characters')
  })

  it('shows the load error inside the dialog with Retry that reloads', async () => {
    api.getBugreport.mockRejectedValueOnce(new Error('500'))
    const w = await open()
    const alert = w.get('[data-load-error]')
    expect(alert.text()).toContain(en.bugreport.loadFailed)
    await alert.get('button').trigger('click')
    await flushPromises()
    expect(api.getBugreport).toHaveBeenCalledTimes(2)
    expect(w.find('[data-load-error]').exists()).toBe(false)
  })

  it('labels the submit button by the chosen method', async () => {
    const w = await open()
    const submit = () => w.findAll('button').filter((b) => b.text().includes(en.bugreport.submitBy.github) || b.text().includes(en.bugreport.submitBy.proxy) || b.text().includes(en.bugreport.submitBy.copy))
    expect(submit()[0]!.text()).toContain(en.bugreport.submitBy.github)
    await w.findAll('button').find((b) => b.text() === en.bugreport.method.copy)!.trigger('click')
    expect(submit()[0]!.text()).toContain(en.bugreport.submitBy.copy)
  })
})
