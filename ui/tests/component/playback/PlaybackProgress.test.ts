import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import PlaybackProgress from '@/components/playback/PlaybackProgress.vue'
import en from '@/i18n/locales/en.json'
import { makeDevice } from '../../fixtures/device'

/* Paused at 0:50 of 3:20: the clock stands still. */
const device = () =>
  makeDevice({ music_assistant: { now_playing: { state: 'paused', elapsed: 50, duration: 200 } } })

function mountBar(props: Record<string, unknown> = {}) {
  return mount(PlaybackProgress, {
    props: { device: device(), ...props },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('PlaybackProgress seek', () => {
  it('is a plain bar when the player cannot seek', () => {
    expect(mountBar().find('[role="slider"]').exists()).toBe(false)
  })

  it('is a slider over the track when it can', () => {
    const slider = mountBar({ seekable: true }).get('[role="slider"]')
    expect(slider.attributes('aria-valuemin')).toBe('0')
    expect(slider.attributes('aria-valuemax')).toBe('200')
    expect(slider.attributes('aria-valuenow')).toBe('50')
    expect(slider.attributes('aria-valuetext')).toBe('0:50 / 3:20')
    expect(slider.attributes('tabindex')).toBe('0')
  })

  it('seeks to the clicked point', async () => {
    const w = mountBar({ seekable: true })
    const slider = w.get('[role="slider"]')
    slider.element.getBoundingClientRect = () => ({ left: 10, width: 200, top: 0, height: 8, right: 210, bottom: 8, x: 10, y: 0, toJSON: () => ({}) })
    await slider.trigger('click', { clientX: 110 })
    expect(w.emitted('seek')).toEqual([[100_000]])
  })

  it("takes the track's length from Music Assistant first, as the seek API checks it", async () => {
    const both = makeDevice({
      playback: { track: { duration_ms: 179_000, progress_ms: 0 } },
      music_assistant: { now_playing: { state: 'paused', elapsed: 50, duration: 180.4 } },
    })
    const w = mountBar({ seekable: true, device: both })
    const slider = w.get('[role="slider"]')
    slider.element.getBoundingClientRect = () => ({ left: 0, width: 100, top: 0, height: 8, right: 100, bottom: 8, x: 0, y: 0, toJSON: () => ({}) })
    await slider.trigger('click', { clientX: 100 })
    expect(w.emitted('seek')).toEqual([[180_400]])
  })

  it('steps with the arrow keys and jumps to the start with Home', async () => {
    const w = mountBar({ seekable: true })
    const slider = w.get('[role="slider"]')
    await slider.trigger('keydown', { key: 'ArrowRight' })
    await slider.trigger('keydown', { key: 'ArrowLeft' })
    await slider.trigger('keydown', { key: 'Home' })
    expect(w.emitted('seek')).toEqual([[55_000], [45_000], [0]])
  })

  it('stays put while a command is pending', async () => {
    const w = mountBar({ seekable: true, disabled: true })
    const slider = w.get('[role="slider"]')
    expect(slider.attributes('aria-disabled')).toBe('true')
    await slider.trigger('keydown', { key: 'ArrowRight' })
    await slider.trigger('click', { clientX: 50 })
    expect(w.emitted('seek')).toBeUndefined()
  })
})
