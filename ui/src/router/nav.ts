import type { Component } from 'vue'
import { Speaker, Users, Settings, Activity } from 'lucide-vue-next'

export interface NavItem {
  to: string
  /** Full label (sidebar) and short label (phone bottom bar). */
  label: string
  short: string
  icon: Component
}

/** The four places of the app, shared by the sidebar and the bottom bar. */
export const NAV: readonly NavItem[] = [
  { to: '/', label: 'nav.home', short: 'nav.homeShort', icon: Speaker },
  { to: '/groups', label: 'nav.groups', short: 'nav.groupsShort', icon: Users },
  { to: '/config', label: 'nav.settings', short: 'nav.settingsShort', icon: Settings },
  { to: '/diagnostics', label: 'nav.diagnostics', short: 'nav.diagnosticsShort', icon: Activity },
]

/** Pages that sit under one of the places (a group's sync page belongs to Groups). */
const UNDER: Readonly<Record<string, readonly string[]>> = { '/groups': ['/sync/'] }

export function isActive(path: string, to: string) {
  if (to === '/') return path === '/'
  return path.startsWith(to) || (UNDER[to] ?? []).some((prefix) => path.startsWith(prefix))
}
