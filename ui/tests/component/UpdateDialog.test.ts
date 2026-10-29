import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import UpdateDialog from '@/components/UpdateDialog.vue'
import en from '@/i18n/en.json'

const update = vi.hoisted(() => ({
  showDialog: true, info: { update_available: true, version: '9.9.9' }, latestVersion: '9.9.9', channel: 'stable',
  updateMethod: 'one_click', applying: false, checking: false, loading: false, error: null, releaseNotes: '',
  doApplyUpdate: vi.fn(), checkForUpdates: vi.fn(), updateAvailable: true,
}))
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/stores/update', () => ({ useUpdateStore: () => update }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ version: '1.0.0' }) }))
vi.mock('@/composables/useConfirm', () => confirm)

async function clickApply() {
  const w = mount(UpdateDialog, {
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { Teleport: true } },
  })
  await flushPromises()
  await w.findAll('button').find((b) => b.text().includes(en.update.applyNow))!.trigger('click')
  await flushPromises()
}

describe('UpdateDialog "Update Now"', () => {
  beforeEach(() => vi.clearAllMocks())

  it('says playback stops and how to go back, and does nothing when declined', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    await clickApply()
    const opts = confirm.confirmDialog.mock.calls[0]![0]
    expect(opts.consequences.join(' ')).toMatch(/Playback stops/)
    expect(opts.title).toContain('9.9.9')
    expect(update.doApplyUpdate).not.toHaveBeenCalled()
  })

  it('applies once confirmed', async () => {
    confirm.confirmDialog.mockResolvedValue(true)
    await clickApply()
    expect(update.doApplyUpdate).toHaveBeenCalledTimes(1)
  })
})
