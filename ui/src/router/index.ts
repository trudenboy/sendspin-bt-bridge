import { createRouter, createWebHistory } from 'vue-router'
import { useIngress } from '@/composables/useIngress'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    hideNav?: boolean
  }
}

const router = createRouter({
  history: createWebHistory(useIngress().basePath),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
    },
    {
      path: '/devices',
      name: 'devices',
      component: () => import('@/views/DevicesView.vue'),
    },
    {
      path: '/config',
      name: 'config',
      component: () => import('@/views/ConfigView.vue'),
    },
    {
      path: '/diagnostics',
      name: 'diagnostics',
      component: () => import('@/views/DiagnosticsView.vue'),
    },
    {
      path: '/ma',
      name: 'ma',
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

export default router
