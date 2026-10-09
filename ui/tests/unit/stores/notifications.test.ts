import { describe, it, expect } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useNotificationStore } from '@/stores/notifications'

describe('useNotificationStore', () => {
  it('adds and removes toasts', () => {
    setActivePinia(createPinia())
    const store = useNotificationStore()

    expect(store.toasts).toHaveLength(0)
    store.success('Test message')
    expect(store.toasts).toHaveLength(1)
    expect(store.toasts[0].type).toBe('success')
    expect(store.toasts[0].message).toBe('Test message')

    store.remove(store.toasts[0].id)
    expect(store.toasts).toHaveLength(0)
  })

  it('supports all toast types', () => {
    setActivePinia(createPinia())
    const store = useNotificationStore()

    store.success('s')
    store.error('e')
    store.warning('w')
    store.info('i')

    expect(store.toasts.map((t) => t.type)).toEqual([
      'success',
      'error',
      'warning',
      'info',
    ])
  })
})
