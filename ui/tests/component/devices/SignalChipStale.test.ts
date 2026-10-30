import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SignalChip from '@/components/devices/chips/SignalChip.vue'
import en from '@/i18n/locales/en.json'
import { makeDevice } from '../../fixtures/device'

const NOW = Date.parse('2026-10-14T10:00:00Z')

describe('SignalChip staleness', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('greys the bars once the sample is older than 30 s', async () => {
    const device = makeDevice({ bluetooth: { connected: true, rssi_dbm: -5, rssi_at: new Date(NOW - 5_000).toISOString() } })
    const w = mount(SignalChip, {
      props: { device },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    const chip = () => w.get('[data-chip="signal"]')
    expect(chip().classes()).toContain('text-success')
    expect(w.findAll('[data-signal-bar].is-on')).toHaveLength(3)

    vi.advanceTimersByTime(30_000)
    await nextTick()
    expect(chip().classes()).toContain('text-text-disabled')
    expect(w.findAll('[data-signal-bar].is-on')).toHaveLength(0)
    expect(w.text()).toContain('Signal reading is out of date')
    w.unmount()
  })
})
