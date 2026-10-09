import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getSession, signIn, signOut } from '@/api/auth'
import { setCsrfToken } from '@/api/client'
import type { AuthMethod, SessionState, SignInResult } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const authenticated = ref(false)
  const authEnabled = ref(false)
  const username = ref<string | null>(null)
  const principal = ref<SessionState['principal']>(null)
  const methods = ref<AuthMethod[]>([])
  const haAddon = ref(false)
  const checking = ref(false)
  /** A pending MFA step: the flow to finish with a code. */
  const pendingFlow = ref<{ method: AuthMethod; flowId: string; moduleName: string | null } | null>(null)

  function apply(state: SessionState) {
    authenticated.value = state.authenticated
    authEnabled.value = state.auth_enabled
    username.value = state.user ?? null
    principal.value = state.principal ?? null
    methods.value = state.methods
    haAddon.value = state.ha_addon
    setCsrfToken(state.csrf_token)
  }

  async function checkAuth() {
    checking.value = true
    try {
      apply(await getSession())
    } catch {
      authenticated.value = false
    } finally {
      checking.value = false
    }
  }

  async function login(method: AuthMethod, password: string, user = ''): Promise<SignInResult> {
    const result = await signIn({ method, username: user, password })
    return finish(method, result)
  }

  async function submitCode(code: string): Promise<SignInResult> {
    const flow = pendingFlow.value
    if (!flow) throw new Error('No sign-in waiting for a code')
    const result = await signIn({ method: flow.method, flow_id: flow.flowId, code })
    return finish(flow.method, result)
  }

  function finish(method: AuthMethod, result: SignInResult) {
    setCsrfToken(result.csrf_token)
    if (result.status === 'signed_in') {
      authenticated.value = true
      username.value = result.user ?? null
      pendingFlow.value = null
    } else {
      pendingFlow.value = { method, flowId: result.flow_id ?? '', moduleName: result.mfa_module_name ?? null }
    }
    return result
  }

  async function logout() {
    await signOut()
    authenticated.value = false
    username.value = null
    await checkAuth()
  }

  return {
    authenticated,
    authEnabled,
    username,
    principal,
    methods,
    haAddon,
    checking,
    pendingFlow,
    checkAuth,
    login,
    submitCode,
    logout,
  }
})
