/**
 * First path segments of the app's routes (current and redirected old ones).
 * useIngress strips the first of these it finds to recover the HA ingress
 * prefix, so a route missing here makes the app treat its own page as the
 * base path. A unit test checks the router against this list.
 */
export const APP_SEGMENTS = ['groups', 'config', 'settings', 'diagnostics', 'login', 'devices', 'ma', 'dashboard'] as const
