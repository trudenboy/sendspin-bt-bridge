import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import router from './router'
import App from './App.vue'
import { setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import en from './i18n/en.json'
import ru from './i18n/ru.json'
import './app.css'

const i18n = createI18n({
  legacy: false,
  locale: localStorage.getItem('sendspin-ui:locale') || 'en',
  fallbackLocale: 'en',
  messages: { en, ru },
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
// An expired session answers 401: back to the sign-in page.
setUnauthorizedHandler(() => {
  useAuthStore().authenticated = false
  if (router.currentRoute.value.name !== 'login') void router.push({ name: 'login' })
})

app.mount('#app')
