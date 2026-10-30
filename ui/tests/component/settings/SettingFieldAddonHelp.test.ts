import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import SettingField from '@/components/settings/SettingField.vue'
import { useAuthStore } from '@/stores/auth'
import { useConfigStore } from '@/stores/config'
import en from '@/i18n/locales/en.json'

// #476 — the Home Assistant add-on's own `log_level` option is written over
// the bridge's LOG_LEVEL on every restart, so the setting says so there.
const LOG_LEVEL = { key: 'LOG_LEVEL', kind: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] } as const

function mountField(key: 'LOG_LEVEL' | 'TZ' = 'LOG_LEVEL') {
  useConfigStore().config = { LOG_LEVEL: 'INFO', TZ: 'UTC' } as never
  const field = key === 'LOG_LEVEL' ? LOG_LEVEL : ({ key: 'TZ', kind: 'text' } as const)
  return mount(SettingField, {
    props: { field },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('SettingField add-on note', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('tells an add-on user that the add-on option sets the log level on restart', () => {
    useAuthStore().haAddon = true
    expect(mountField().text()).toContain(en.settings.fields.LOG_LEVEL.addonHelp)
  })

  it('shows no add-on note outside the add-on', () => {
    useAuthStore().haAddon = false
    expect(mountField().text()).not.toContain(en.settings.fields.LOG_LEVEL.addonHelp)
  })

  it('adds nothing to a field without an add-on note', () => {
    useAuthStore().haAddon = true
    expect(mountField('TZ').text()).toContain(en.settings.fields.TZ.help)
  })
})
