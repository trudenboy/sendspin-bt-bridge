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
      name: 'home',
      meta: { title: 'nav.home' },
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/groups',
      name: 'groups',
      meta: { title: 'nav.groups' },
      component: () => import('@/views/GroupsView.vue'),
    },
    {
      path: '/config',
      name: 'config',
      meta: { title: 'nav.settings' },
      component: () => import('@/views/ConfigView.vue'),
    },
    {
      path: '/diagnostics',
      name: 'diagnostics',
      meta: { title: 'nav.diagnostics' },
      component: () => import('@/views/DiagnosticsView.vue'),
    },
    // Earlier addresses (bookmarks, the HA panel) keep working.
    { path: '/devices', redirect: '/' },
    { path: '/ma', redirect: '/groups' },
    { path: '/settings', redirect: '/config' },
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
