import { reactive } from 'vue'

export interface ConfirmOptions {
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  /** Destructive: the confirm button is red. */
  danger?: boolean
  /** One line per consequence (which speakers, whether playback stops). */
  consequences?: string[]
  /** A side link in the dialog, e.g. "Download current config first". */
  link?: { label: string; run: () => void }
}

/** The one pending question; ConfirmHost (in App.vue) renders it. */
export const confirmState = reactive<{ open: boolean; options: ConfirmOptions | null; resolve: ((ok: boolean) => void) | null }>({
  open: false,
  options: null,
  resolve: null,
})

/**
 * Asks in the page's own dialog instead of window.confirm, which looks foreign
 * inside Home Assistant and can be suppressed in embedded web views.
 * Components do not call this directly: use confirmAction() from useActionPolicy.
 * Resolves true on confirm, false on cancel/close. A new question cancels a pending one.
 */
export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  confirmState.resolve?.(false)
  return new Promise((resolve) => {
    confirmState.options = options
    confirmState.resolve = resolve
    confirmState.open = true
  })
}

export function settleConfirm(ok: boolean) {
  const resolve = confirmState.resolve
  confirmState.open = false
  confirmState.resolve = null
  resolve?.(ok)
}
