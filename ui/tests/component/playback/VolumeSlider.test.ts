import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import en from '@/i18n/en.json'

function buildI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
}

describe('VolumeSlider', () => {
  function mountSlider(props: Record<string, unknown> = {}) {
    return mount(VolumeSlider, {
      props: {
        mac: 'AA:BB:CC:DD:EE:FF',
        volume: 50,
        muted: false,
        ...props,
      },
      global: { plugins: [buildI18n()] },
    })
  }

  it('renders slider with volume value', () => {
    const w = mountSlider()
    expect(w.find('input[type="range"]').exists()).toBe(true)
    expect(w.text()).toContain('50%')
  })

  it('shows muted icon when muted', () => {
    const w = mountSlider({ muted: true })
    expect(w.find('.text-warning').exists()).toBe(true)
  })

  it('emits update:muted on mute toggle', async () => {
    const w = mountSlider()
    await w.find('button').trigger('click')
    expect(w.emitted('update:muted')?.[0]).toEqual([true])
  })

  it('emits update:muted false when already muted', async () => {
    const w = mountSlider({ muted: true })
    await w.find('button').trigger('click')
    expect(w.emitted('update:muted')?.[0]).toEqual([false])
  })

  it('emits update:volume with debounce', async () => {
    vi.useFakeTimers()
    const w = mountSlider()
    await w.find('input[type="range"]').setValue('75')
    expect(w.emitted('update:volume')).toBeFalsy()
    vi.advanceTimersByTime(300)
    expect(w.emitted('update:volume')?.[0]).toEqual([75])
    vi.useRealTimers()
  })

  it('displays volume percentage', () => {
    const w = mountSlider({ volume: 82 })
    expect(w.text()).toContain('82%')
  })

  it('disables slider when disabled prop is true', () => {
    const w = mountSlider({ disabled: true })
    expect(w.find('input[type="range"]').attributes('disabled')).toBeDefined()
  })

  it('disables slider when muted', () => {
    const w = mountSlider({ muted: true })
    expect(w.find('input[type="range"]').attributes('disabled')).toBeDefined()
  })

  it('updates local volume when prop changes', async () => {
    const w = mountSlider({ volume: 30 })
    expect(w.text()).toContain('30%')
    await w.setProps({ volume: 70 })
    expect(w.text()).toContain('70%')
  })
})
