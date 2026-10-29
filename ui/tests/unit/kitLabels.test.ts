import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import SbDialog from '@/kit/SbDialog.vue'
import SbDrawer from '@/kit/SbDrawer.vue'
import SbToast from '@/kit/SbToast.vue'
import { KIT_LABELS_KEY, kitLabelsFrom } from '@/kit/labels'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

const stubs = { Teleport: true }

describe('kit labels', () => {
  it('default to English without a provider', () => {
    const w = mount(SbDialog, { props: { modelValue: true, title: 'T' }, global: { stubs } })
    expect(w.get('[data-testid="dialog-close-btn"]').attributes('aria-label')).toBe('Close dialog')
  })

  it('are taken from the provided translations', () => {
    const provide = { [KIT_LABELS_KEY as symbol]: ref({ ...kitLabelsFrom((k) => `x:${k}`) }) }
    const dialog = mount(SbDialog, { props: { modelValue: true, title: 'T' }, global: { stubs, provide } })
    expect(dialog.get('[data-testid="dialog-close-btn"]').attributes('aria-label')).toBe('x:kit.closeDialog')
    const drawer = mount(SbDrawer, { props: { modelValue: true, title: 'T' }, global: { stubs, provide } })
    expect(drawer.get('[data-testid="drawer-close-btn"]').attributes('aria-label')).toBe('x:kit.closeDrawer')
    const toast = mount(SbToast, { props: { id: 1, message: 'm' }, global: { provide } })
    expect(toast.get('[data-testid="toast-close-btn"]').attributes('aria-label')).toBe('x:kit.dismissNotification')
  })

  it('are translated in Russian', () => {
    const t = createI18n({ legacy: false, locale: 'ru', messages: { en, ru } }).global.t
    const labels = kitLabelsFrom(t as never)
    for (const v of Object.values(labels)) expect(v).not.toMatch(/^kit\./)
    expect(labels.closeDialog).not.toBe(en.kit.closeDialog)
  })
})
