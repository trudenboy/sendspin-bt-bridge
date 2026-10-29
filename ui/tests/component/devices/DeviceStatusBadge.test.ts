import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DeviceStatusBadge from '@/components/devices/DeviceStatusBadge.vue'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

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

  it.each([
    ['streaming', 'Playing', 'success'],
    ['ready', 'Connected', 'neutral'],
    ['transitioning', 'Connecting…', 'warning'],
    ['recovering', 'Reconnecting…', 'warning'],
    ['degraded', 'Problem', 'error'],
    ['offline', 'Not connected', 'neutral'],
    ['standby', 'Standby', 'neutral'],
    ['disabled', 'Disabled', 'neutral'],
  ])('renders %s as %s with %s tone', (state, label, tone) => {
    const w = mountBadge(state)
    expect(w.text()).toContain(label)
    expect(w.find(`.tone-${tone}`).exists()).toBe(true)
  })

  it('falls back to raw state for unknown values', () => {
    const w = mountBadge('CUSTOM_STATE')
    expect(w.text()).toContain('CUSTOM_STATE')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })

  it('contains SbStatusDot element', () => {
    const w = mountBadge('streaming')
    expect(w.find('[role="status"]').exists()).toBe(true)
  })
})

describe('DeviceStatusBadge: runtime states', () => {
  it.each([
    ['buffering', 'Buffering', 'warning'],
    ['sink_muted', 'Muted by system', 'warning'],
    ['stopping', 'Stopping', 'warning'],
    ['reconnecting', 'Reconnecting', 'warning'],
  ])('renders %s as %s with %s tone', (state, label, tone) => {
    const w = mount(DeviceStatusBadge, {
      props: { state },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(w.text()).toContain(label)
    expect(w.find(`.tone-${tone}`).exists()).toBe(true)
  })
})

describe('DeviceStatusBadge: standby time and release kind', () => {
  const i18n = () => createI18n({ legacy: false, locale: 'en', messages: { en } })
  const NOW = Date.parse('2026-10-14T10:00:00Z')

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const mountWith = (state: string, device: Device) =>
    mount(DeviceStatusBadge, { props: { state, device }, global: { plugins: [i18n()] } })

  it('says how long the speaker has been on standby and keeps counting', async () => {
    const w = mountWith('standby', makeDevice({ bluetooth: { standby: true, standby_since: '2026-10-14T09:48:00Z' } }))
    expect(w.text()).toContain('Standby · 12 min')
    vi.advanceTimersByTime(60_000)
    await nextTick()
    expect(w.text()).toContain('Standby · 13 min')
    w.unmount()
  })

  it('switches to hours past an hour, and says under a minute at first', () => {
    expect(
      mountWith('standby', makeDevice({ bluetooth: { standby: true, standby_since: '2026-10-14T08:45:00Z' } })).text(),
    ).toContain('Standby · 1 h 15 min')
    expect(
      mountWith('standby', makeDevice({ bluetooth: { standby: true, standby_since: '2026-10-14T09:59:40Z' } })).text(),
    ).toContain('Standby · <1 min')
  })

  it('stays plain Standby without a start time', () => {
    const w = mountWith('standby', makeDevice({ bluetooth: { standby: true, standby_since: null } }))
    expect(w.text().trim()).toBe('Standby')
  })

  it('says Handed over automatically (warning) when the bridge let go by itself, Handed over (neutral) otherwise', () => {
    const auto = mountWith('released', makeDevice({ bluetooth: { management_enabled: false, released_by: 'auto' } }))
    expect(auto.text()).toContain('Handed over automatically')
    expect(auto.find('.tone-warning').exists()).toBe(true)
    const user = mountWith('released', makeDevice({ bluetooth: { management_enabled: false, released_by: 'user' } }))
    expect(user.text()).toContain('Handed over')
    expect(user.text()).not.toContain('Handed over automatically')
    expect(user.find('.tone-neutral').exists()).toBe(true)
  })
})
