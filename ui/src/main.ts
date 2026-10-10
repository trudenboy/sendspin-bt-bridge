import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import { setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import { i18n } from './i18n'
import './app.css'

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
