import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import SettingField from '@/components/settings/SettingField.vue'
import { ALL_FIELDS } from '@/settings/layout'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'

const field = ALL_FIELDS.find((f) => f.key === 'PLAYOUT_SYNC')!

function optionsFor(stored: string) {
  const w = mount(SettingField, {
    props: { field, read: () => stored, write: () => undefined },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  return w.findAll('option').map((o) => o.attributes('value'))
}

describe('PLAYOUT_SYNC setting (Stage 1)', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('offers off, measure and loop (Stage B1 shipped loop mode)', () => {
    expect(field.offered).toBeUndefined()
    expect(optionsFor('off')).toEqual(['off', 'measure', 'loop'])
  })

  it('explains loop in the help text, in both languages', () => {
    expect(en.settings.fields.PLAYOUT_SYNC.help.toLowerCase()).toContain('air sensor')
    expect(ru.settings.fields.PLAYOUT_SYNC.help).toContain('датчик эфира')
  })

  it('describes the per-speaker opt-out by what Stage 1 does (no hold, no offset)', () => {
    const enHelp = JSON.stringify(en).match(/"playoutSyncHelp":"([^"]*)"/)![1].toLowerCase()
    const ruHelp = JSON.stringify(ru).match(/"playoutSyncHelp":"([^"]*)"/)![1].toLowerCase()
    for (const word of ['warm', 'offset']) expect(enHelp, word).not.toContain(word)
    for (const word of ['прогрет', 'смещени']) expect(ruHelp, word).not.toContain(word)
  })
})
