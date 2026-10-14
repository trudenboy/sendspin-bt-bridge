import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SyncChip from '@/components/devices/chips/SyncChip.vue'
import { resetReanchorMemory } from '@/utils/reanchorHold'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

const dev = (reanchoring: boolean) =>
  makeDevice({ id: 'hold', playback: { playing: true }, audio: { streaming: true }, timing: { available: true, reanchoring } })

describe('SyncChip re-anchoring hold', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetReanchorMemory()
  })
  afterEach(() => vi.useRealTimers())

  it('keeps "Re-anchoring" up for 5 s after it clears, then shows "Sync"', async () => {
    const w = mount(SyncChip, {
      props: { device: dev(true) },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(w.get('[data-chip="sync"]').text()).toContain('Re-anchoring')
    await w.setProps({ device: dev(false) })
    expect(w.get('[data-chip="sync"]').text()).toContain('Re-anchoring')
    await vi.advanceTimersByTimeAsync(4000)
    expect(w.get('[data-chip="sync"]').text()).toContain('Re-anchoring')
    await vi.advanceTimersByTimeAsync(1100)
    expect(w.get('[data-chip="sync"]').text()).not.toContain('Re-anchoring')
    expect(w.get('[data-chip="sync"]').text()).toContain('Sync')
  })

  it('does not flash "Re-anchoring" when a chip comes back after re-anchors it did not see', () => {
    const counted = (n: number) =>
      makeDevice({ id: 'back', playback: { playing: true }, audio: { streaming: true }, timing: { available: true, reanchor_count: n, last_reanchor_at: n } })
    const opts = { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } }
    mount(SyncChip, { props: { device: counted(2) }, ...opts }).unmount()
    const again = mount(SyncChip, { props: { device: counted(5) }, ...opts })
    expect(again.get('[data-chip="sync"]').text()).not.toContain('Re-anchoring')
  })
})
