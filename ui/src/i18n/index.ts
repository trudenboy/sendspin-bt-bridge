import { createI18n } from 'vue-i18n'
import en from './en.json'
import ru from './ru.json'

function storedLocale() {
  try {
    return localStorage.getItem('sendspin-ui:locale') || 'en'
  } catch {
    return 'en'
  }
}

/** The app's one i18n instance; stores use ``i18n.global.t`` outside components. */
export const i18n = createI18n({
  legacy: false,
  locale: storedLocale(),
  fallbackLocale: 'en',
  messages: { en, ru },
})
