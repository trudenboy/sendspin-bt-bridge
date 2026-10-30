/**
 * The "Show technical details" preference.
 *
 * Off hides the technical chips and rows (signal Δ dB, re-anchor counts,
 * Playout, server ID, signal path, sync diagnostics, advanced settings).
 * A browser's own choice is kept in localStorage; until it makes one, the
 * bridge's default applies (``SHOW_TECHNICAL_DETAILS``: off for a new install,
 * on for an install that already had speakers). The old Settings-only
 * "advanced" switch counts as a choice, so nobody loses what they had open.
 */
import { computed, ref } from 'vue'

const KEY = 'sendspin-ui:technical-details'
const LEGACY_KEY = 'sendspin-ui:settings-advanced'

function stored(): boolean | null {
  try {
    const v = localStorage.getItem(KEY) ?? localStorage.getItem(LEGACY_KEY)
    return v === '1' ? true : v === '0' ? false : null
  } catch {
    return null
  }
}

const own = ref<boolean | null>(stored())
const serverDefault = ref<boolean | null>(null)

export const technicalDetails = computed(() => own.value ?? serverDefault.value ?? false)

export function setTechnicalDetails(on: boolean | null) {
  own.value = on
  try {
    if (on === null) localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, on ? '1' : '0')
  } catch {
    /* remembered for this visit only */
  }
}

/** The bridge's default, from its saved configuration. */
export function setTechnicalDetailsDefault(on: boolean | null | undefined) {
  serverDefault.value = typeof on === 'boolean' ? on : null
}

/** Re-reads the browser's choice (tests, and a storage change from another tab). */
export function reloadTechnicalDetails() {
  own.value = stored()
}

export function useTechnicalDetails() {
  return { technicalDetails, setTechnicalDetails }
}
