import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DeviceStatusBadge from '@/components/devices/DeviceStatusBadge.vue'
import en from '@/i18n/en.json'

function buildI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
}

describe('DeviceStatusBadge', () => {
  function mountBadge(state: string) {
    return mount(DeviceStatusBadge, {
      props: { state },
      global: { plugins: [buildI18n()] },
    })
  }

  it('renders STREAMING with success tone', () => {
    const w = mountBadge('STREAMING')
    expect(w.text()).toContain('Streaming')
    expect(w.find('.tone-success').exists()).toBe(true)
  })

  it('renders READY with info tone', () => {
    const w = mountBadge('READY')
    expect(w.text()).toContain('Ready')
    expect(w.find('.tone-info').exists()).toBe(true)
  })

  it('renders CONNECTING with warning tone', () => {
    const w = mountBadge('CONNECTING')
    expect(w.text()).toContain('Connecting')
    expect(w.find('.tone-warning').exists()).toBe(true)
  })

  it('renders ERROR with error tone', () => {
    const w = mountBadge('ERROR')
    expect(w.text()).toContain('Error')
    expect(w.find('.tone-error').exists()).toBe(true)
  })

  it('renders OFFLINE with neutral tone', () => {
    const w = mountBadge('OFFLINE')
    expect(w.text()).toContain('Offline')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })

  it('renders STANDBY with neutral tone', () => {
    const w = mountBadge('STANDBY')
    expect(w.text()).toContain('Standby')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })

  it('renders IDLE with neutral tone', () => {
    const w = mountBadge('IDLE')
    expect(w.text()).toContain('Idle')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })

  it('falls back to raw state for unknown values', () => {
    const w = mountBadge('CUSTOM_STATE')
    expect(w.text()).toContain('CUSTOM_STATE')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })

  it('contains SbStatusDot element', () => {
    const w = mountBadge('STREAMING')
    expect(w.find('[role="status"]').exists()).toBe(true)
  })
})
