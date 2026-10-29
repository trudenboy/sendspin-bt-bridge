import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: number
  type: ToastType
  message: string
  duration: number
  /** A button on the toast (Undo); running it also dismisses the toast. */
  action?: { label: string; run: () => void | Promise<unknown> }
}

let nextId = 0

export const useNotificationStore = defineStore('notifications', () => {
  const toasts = ref<Toast[]>([])

  // The toast component owns the countdown, so hovering or focusing it can
  // pause it. ``duration`` 0 keeps a toast until it is dismissed (errors).
  function add(type: ToastType, message: string, duration = 4000, action?: Toast['action']) {
    const id = nextId++
    toasts.value.push({ id, type, message, duration, action })
  }

  function remove(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  const hasToasts = computed(() => toasts.value.length > 0)

  return {
    toasts,
    hasToasts,
    success: (msg: string) => add('success', msg),
    error: (msg: string) => add('error', msg, 0),
    warning: (msg: string) => add('warning', msg),
    info: (msg: string) => add('info', msg),
    /** A done, reversible action: a success toast with an Undo button that stays long enough to use. */
    undo: (msg: string, label: string, run: NonNullable<Toast['action']>['run']) => add('success', msg, 8000, { label, run }),
    remove,
  }
})
