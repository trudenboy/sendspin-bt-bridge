import { onScopeDispose, ref } from 'vue'

/**
 * Whether a CSS media query matches, kept up to date. Without matchMedia
 * (tests, very old browsers) it reports ``fallback``.
 */
export function useMediaQuery(query: string, fallback = true) {
  const mql = typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
  const matches = ref(mql ? mql.matches : fallback)
  if (mql) {
    const onChange = (e: MediaQueryListEvent) => (matches.value = e.matches)
    mql.addEventListener?.('change', onChange)
    onScopeDispose(() => mql.removeEventListener?.('change', onChange))
  }
  return matches
}
