import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useNotificationStore } from '@/stores/notifications'
import SbToastContainer from '@/kit/SbToastContainer.vue'

function mountContainer() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(SbToastContainer, {
    global: { plugins: [pinia] },
  })
}

describe('SbToastContainer', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders without toasts', () => {
    const w = mountContainer()
    expect(w.findAll('[role="alert"]')).toHaveLength(0)
  })

  it('renders toasts from the store', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useNotificationStore()
    store.success('Message 1')
    store.error('Message 2')

    const w = mount(SbToastContainer, {
      global: { plugins: [pinia] },
    })

    expect(w.findAll('[role="alert"]')).toHaveLength(2)
    expect(w.text()).toContain('Message 1')
    expect(w.text()).toContain('Message 2')
  })

  it('has fixed positioning classes', () => {
    const w = mountContainer()
    expect(w.classes()).toContain('fixed')
    expect(w.classes()).toContain('z-[100]')
  })
})
