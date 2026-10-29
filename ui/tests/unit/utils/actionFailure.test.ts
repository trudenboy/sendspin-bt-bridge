import { describe, it, expect } from 'vitest'
import { createI18n } from 'vue-i18n'
import { actionFailure } from '@/utils/actionFailure'
import { ApiError } from '@/api/client'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

const i18n = (locale: 'en' | 'ru') => createI18n({ legacy: false, locale, messages: { en, ru } }).global.t

describe('actionFailure', () => {
  it('names the action and the speaker', () => {
    expect(actionFailure(i18n('en'), 'volume', 'Kitchen', new Error('x'))).toBe('Could not change the volume of Kitchen')
    expect(actionFailure(i18n('en'), 'mute', 'Kitchen', null)).toBe('Could not mute or unmute Kitchen')
    expect(actionFailure(i18n('en'), 'release', 'Kitchen', null)).toBe('Could not hand over Kitchen')
    expect(actionFailure(i18n('en'), 'claim', 'Kitchen', null)).toBe('Could not take back Kitchen')
  })

  it('appends the server reason of an ApiError', () => {
    const e = new ApiError({ status: 409, code: 'busy', detail: 'Speaker is not connected' })
    expect(actionFailure(i18n('en'), 'volume', 'Kitchen', e)).toBe('Could not change the volume of Kitchen: Speaker is not connected')
  })

  it('is translated', () => {
    expect(actionFailure(i18n('ru'), 'volume', 'Кухня', null)).not.toMatch(/Could not|device\.actions/)
  })
})
