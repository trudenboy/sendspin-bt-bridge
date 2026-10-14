import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import RuntimeRow from '@/components/layout/RuntimeRow.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

const connected = (id: string) => makeDevice({ id, bluetooth: { connected: true } })
const offline = (id: string) => makeDevice({ id, bluetooth: { connected: false } })
const playing = (id: string) =>
  makeDevice({ id, bluetooth: { connected: true }, playback: { playing: true }, audio: { streaming: true } })

function mountRow(devices: Device[], bridge: Record<string, unknown> = {}) {
  const store = useBridgeStore()
  const status = makeStatus(devices)
  Object.assign(status.bridge, bridge)
  store.snapshot = status
  store.loading = false
  return mount(RuntimeRow, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
}

const pill = (w: ReturnType<typeof mountRow>, key: string) => w.find(`[data-runtime="${key}"]`)

describe('RuntimeRow', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('names the runtime, host, address and uptime', () => {
    const w = mountRow([connected('a')], { runtime: 'ha_addon', hostname: 'pi4', ip_address: '10.0.0.5', uptime: '1 day, 2:03:04' })
    expect(pill(w, 'runtime').text()).toBe('Home Assistant add-on')
    expect(pill(w, 'host').text()).toBe('pi4')
    expect(pill(w, 'ip').text()).toBe('10.0.0.5')
    expect(pill(w, 'uptime').text()).toBe('up 1 day, 2:03:04')
  })

  it('flags a non-production runtime mode and hides empty facts', () => {
    const w = mountRow([connected('a')], { runtime: 'docker', runtime_mode: 'demo', hostname: '', ip_address: '', uptime: '' })
    expect(pill(w, 'runtime').text()).toBe('Docker')
    expect(pill(w, 'mode').text()).toBe('demo')
    expect(pill(w, 'host').exists()).toBe(false)
    expect(pill(w, 'ip').exists()).toBe(false)
    expect(pill(w, 'uptime').exists()).toBe(false)
    expect(pill(mountRow([connected('a')]), 'mode').exists()).toBe(false)
  })

  it('counts connected speakers, coloured by how many are up', () => {
    expect(pill(mountRow([connected('a'), connected('b')]), 'speakers').text()).toBe('2/2 speakers connected')
    expect(pill(mountRow([connected('a'), connected('b')]), 'speakers').classes()).toContain('tone-success')
    expect(pill(mountRow([connected('a'), offline('b')]), 'speakers').classes()).toContain('tone-warning')
    expect(pill(mountRow([offline('a')]), 'speakers').classes()).toContain('tone-error')
    expect(pill(mountRow([]), 'speakers').exists()).toBe(false)
  })

  it('shows Music Assistant and how many speakers play', () => {
    const on = mountRow([playing('a'), connected('b')], { ma_connected: true })
    expect(pill(on, 'ma').text()).toBe('Music Assistant connected')
    expect(pill(on, 'ma').classes()).toContain('tone-success')
    expect(pill(on, 'playing').text()).toBe('1 playing')
    const off = mountRow([connected('a')], { ma_connected: false })
    expect(pill(off, 'ma').text()).toBe('Music Assistant not connected')
    expect(pill(off, 'ma').classes()).toContain('tone-neutral')
    expect(pill(off, 'playing').exists()).toBe(false)
  })

  it('counts standby as fine and leaves disabled and handed-over speakers out, as stable does', () => {
    const parked = (id: string) => makeDevice({ id, bluetooth: { connected: false, standby: true } })
    const night = pill(mountRow([parked('a'), parked('b')]), 'speakers')
    expect(night.classes()).toContain('tone-success')
    const w = mountRow([
      connected('a'),
      makeDevice({ id: 'd', enabled: false, bluetooth: { connected: false } }),
      makeDevice({ id: 'r', bluetooth: { connected: false, management_enabled: false, released_by: 'user' } }),
    ])
    expect(pill(w, 'speakers').text()).toBe('1/1 speakers connected')
    expect(pill(w, 'speakers').classes()).toContain('tone-success')
    const auto = mountRow([connected('a'), makeDevice({ id: 'x', bluetooth: { connected: false, management_enabled: false, released_by: 'auto' } })])
    expect(pill(auto, 'speakers').text()).toBe('1/2 speakers connected')
    expect(pill(auto, 'speakers').classes()).toContain('tone-warning')
  })
})
