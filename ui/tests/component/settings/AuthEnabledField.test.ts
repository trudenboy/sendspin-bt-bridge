import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import SettingField from '@/components/settings/SettingField.vue'
import { useConfigStore } from '@/stores/config'
import { ALL_FIELDS } from '@/settings/layout'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

const field = ALL_FIELDS.find((f) => f.key === 'AUTH_ENABLED')!

function mountField(config: Record<string, unknown>) {
  setActivePinia(createPinia())
  ;(useConfigStore() as unknown as { config: unknown }).config = config
  return mount(SettingField, {
    props: { field },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('AUTH_ENABLED toggle', () => {
  it('is disabled with a "set a password first" hint until a password exists', () => {
    const w = mountField({ AUTH_ENABLED: false, _password_set: false })
    expect(w.get('[role="switch"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.settings.fields.AUTH_ENABLED.disabledReason)
  })

  it('is enabled once a password is set', () => {
    const w = mountField({ AUTH_ENABLED: false, _password_set: true })
    expect(w.get('[role="switch"]').attributes('disabled')).toBeUndefined()
  })

  it('can always be turned off', () => {
    const w = mountField({ AUTH_ENABLED: true, _password_set: false })
    expect(w.get('[role="switch"]').attributes('disabled')).toBeUndefined()
  })

  it('has the hint in Russian', () => {
    expect(ru.settings.fields.AUTH_ENABLED.disabledReason).toBeTruthy()
  })
})
