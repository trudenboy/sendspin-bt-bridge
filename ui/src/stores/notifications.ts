import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: number
  type: ToastType
  message: string
  duration: number
}

let nextId = 0

export const useNotificationStore = defineStore('notifications', () => {
  const toasts = ref<Toast[]>([])

  function add(type: ToastType, message: string, duration = 4000) {
    const id = nextId++
    toasts.value.push({ id, type, message, duration })

    if (duration > 0) {
      setTimeout(() => remove(id), duration)
    }
  }

  function remove(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  const hasToasts = computed(() => toasts.value.length > 0)

  return {
    toasts,
    hasToasts,
    success: (msg: string) => add('success', msg),
    error: (msg: string) => add('error', msg, 6000),
    warning: (msg: string) => add('warning', msg),
    info: (msg: string) => add('info', msg),
    remove,
  }
})
