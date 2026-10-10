import { createRouter, createWebHistory } from 'vue-router'
import { useIngress } from '@/composables/useIngress'
import { useAuthStore } from '@/stores/auth'
import { useBridgeStore } from '@/stores/bridge'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    hideNav?: boolean
    /** i18n key of the page name, used in the browser tab title. */
    title?: string
  }
}

const router = createRouter({
  history: createWebHistory(useIngress().basePath),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      meta: { title: 'app.dashboard' },
      component: () => import('@/views/DashboardView.vue'),
    },
    {
      path: '/devices',
      name: 'devices',
      meta: { title: 'app.devices' },
      component: () => import('@/views/DevicesView.vue'),
    },
    {
      path: '/config',
      name: 'config',
      meta: { title: 'app.config' },
      component: () => import('@/views/ConfigView.vue'),
    },
    {
      path: '/diagnostics',
      name: 'diagnostics',
      meta: { title: 'app.diagnostics' },
      component: () => import('@/views/DiagnosticsView.vue'),
    },
    {
      path: '/ma',
      name: 'ma',
      meta: { title: 'app.ma' },
      component: () => import('@/views/MAView.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { hideNav: true },
    },
  ],
})

let sessionChecked = false

/** Sign-in first when the bridge asks for it; the live stream once signed in. */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!sessionChecked) {
    await auth.checkAuth()
    sessionChecked = true
  }
  const needsSignIn = auth.authEnabled && !auth.authenticated
  if (needsSignIn && to.name !== 'login') return { name: 'login' }
  if (!needsSignIn && to.name === 'login') return { name: 'dashboard' }
  if (!needsSignIn) {
    const bridge = useBridgeStore()
    if (!bridge.sseConnected && !bridge.snapshot) bridge.connectSSE()
  }
  return true
})

export default router
