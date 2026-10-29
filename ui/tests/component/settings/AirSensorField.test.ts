import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import SettingField from '@/components/settings/SettingField.vue'
import { useConfigStore } from '@/stores/config'
import { ALL_FIELDS } from '@/settings/layout'
import en from '@/i18n/en.json'

const field = ALL_FIELDS.find((f) => f.key === 'EXPERIMENTAL_AIR_SENSOR')!

function mountField(config: Record<string, unknown>) {
  setActivePinia(createPinia())
  ;(useConfigStore() as unknown as { config: unknown }).config = config
  return mount(SettingField, {
    props: { field },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}
const toggle = (w: ReturnType<typeof mountField>) => w.find('[role="switch"]')

describe('EXPERIMENTAL_AIR_SENSOR toggle', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('is disabled, with the reason, while playout sync is off', () => {
    const w = mountField({ PLAYOUT_SYNC: 'off', EXPERIMENTAL_AIR_SENSOR: false })
    expect(toggle(w).attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.settings.fields.EXPERIMENTAL_AIR_SENSOR.disabledReason)
  })

  it.each(['measure', 'loop'])('is enabled in %s mode', (mode) => {
    const w = mountField({ PLAYOUT_SYNC: mode, EXPERIMENTAL_AIR_SENSOR: false })
    expect(toggle(w).attributes('disabled')).toBeUndefined()
    expect(w.text()).not.toContain(en.settings.fields.EXPERIMENTAL_AIR_SENSOR.disabledReason)
  })

  it('can still be turned off when it is on but sync was switched off', () => {
    const w = mountField({ PLAYOUT_SYNC: 'off', EXPERIMENTAL_AIR_SENSOR: true })
    expect(toggle(w).attributes('disabled')).toBeUndefined()
  })
})
