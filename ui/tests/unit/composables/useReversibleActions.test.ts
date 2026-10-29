import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { createApp } from 'vue'
import { useReversibleActions } from '@/composables/useReversibleActions'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import en from '@/i18n/en.json'

const store = vi.hoisted(() => ({ release: vi.fn(), setEnabled: vi.fn() }))
vi.mock('@/stores/devices', () => ({ useDeviceStore: () => store }))

function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  let api!: ReturnType<typeof useReversibleActions>
  const app = createApp({ setup() { api = useReversibleActions(); return () => null } })
  app.use(pinia).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  app.mount(document.createElement('div'))
  return { api, notes: useNotificationStore() }
}

describe('useReversibleActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    store.release.mockResolvedValue(undefined)
    store.setEnabled.mockResolvedValue(undefined)
  })

  it('hands over at once and offers Undo, which takes the speaker back', async () => {
    const { api, notes } = setup()
    await api.handOver('d1', 'Kitchen')
    expect(store.release).toHaveBeenCalledWith('d1', true)
    const toast = notes.toasts[0]!
    expect(toast.message).toContain('Kitchen')
    expect(toast.action?.label).toBe(en.undo.label)
    await toast.action!.run()
    expect(store.release).toHaveBeenLastCalledWith('d1', false)
  })

  it('disables at once and Undo enables again', async () => {
    const { api, notes } = setup()
    await api.disable('d1', 'Kitchen')
    expect(store.setEnabled).toHaveBeenCalledWith('d1', false)
    await notes.toasts[0]!.action!.run()
    expect(store.setEnabled).toHaveBeenLastCalledWith('d1', true)
  })

  it('shows a specific error and no Undo when the action fails', async () => {
    store.release.mockRejectedValue(new ApiError({ status: 409, code: 'x', detail: 'busy' }))
    const { api, notes } = setup()
    await api.handOver('d1', 'Kitchen')
    expect(notes.toasts).toHaveLength(1)
    expect(notes.toasts[0]!.type).toBe('error')
    expect(notes.toasts[0]!.message).toBe('Could not release Kitchen: busy')
  })
})
