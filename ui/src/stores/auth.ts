import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  checkAuth as apiCheckAuth,
  login as apiLogin,
  logout as apiLogout,
} from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const authenticated = ref(false)
  const username = ref<string | null>(null)
  const haUser = ref(false)
  const checking = ref(false)

  async function checkAuth() {
    checking.value = true
    try {
      const data = await apiCheckAuth()
      authenticated.value = data.authenticated
      username.value = data.username ?? null
      haUser.value = data.ha_user ?? false
    } catch {
      authenticated.value = false
    } finally {
      checking.value = false
    }
  }

  async function login(password: string) {
    const result = await apiLogin(password)
    if (result.success) {
      authenticated.value = true
    }
    return result
  }

  async function logout() {
    await apiLogout()
    authenticated.value = false
    username.value = null
    haUser.value = false
  }

  function setAuthenticated(value: boolean) {
    authenticated.value = value
  }

  return {
    authenticated,
    username,
    haUser,
    checking,
    checkAuth,
    login,
    logout,
    setAuthenticated,
  }
})
