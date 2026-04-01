import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import router from './router'
import App from './App.vue'
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
app.mount('#app')
