import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import VolumeSlider, { tapStep } from '@/components/playback/VolumeSlider.vue'
import en from '@/i18n/en.json'

describe('tapStep', () => {
  it('moves one step toward the tapped side', () => {
    expect(tapStep(40, 95)).toBe(45)
    expect(tapStep(40, 5)).toBe(35)
  })
  it('stays inside 0–100 and ignores a tap on the thumb', () => {
    expect(tapStep(98, 100)).toBe(100)
    expect(tapStep(2, 0)).toBe(0)
    expect(tapStep(40, 40)).toBe(40)
  })
})

function mountSlider() {
  return mount(VolumeSlider, {
    props: { mac: 'AA', volume: 40, muted: false },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('VolumeSlider on a touch screen', () => {
  it('steps instead of jumping to where a finger taps', async () => {
    const w = mountSlider()
    const range = w.get('input[type="range"]')
    await range.trigger('pointerdown', { pointerType: 'touch', clientX: 100 })
    await range.setValue(100) // the browser jumps the thumb to the tap
    await range.trigger('pointerup', { pointerType: 'touch', clientX: 101 })

    expect(w.emitted('update:volume')).toEqual([[45]])
    expect(w.text()).toContain('45%')
  })

  it('still follows a finger that drags', async () => {
    const w = mountSlider()
    const range = w.get('input[type="range"]')
    await range.trigger('pointerdown', { pointerType: 'touch', clientX: 100 })
    await range.trigger('pointermove', { pointerType: 'touch', clientX: 160 })
    await range.setValue(70)
    await range.trigger('pointerup', { pointerType: 'touch', clientX: 160 })
    await new Promise((r) => setTimeout(r, 350))

    expect(w.emitted('update:volume')).toEqual([[70]])
  })
})
