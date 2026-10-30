import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useAudioStore } from '@/stores/audio'
import en from '@/i18n/locales/en.json'
import type { Device, Group } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => ({ setVolume: vi.fn(), setMute: vi.fn(), release: vi.fn(), setEnabled: vi.fn() }),
}))
vi.mock('@/stores/notifications', () => ({
  useNotificationStore: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))

const Stub = { template: '<div />' }

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: Stub },
      { path: '/config', name: 'config', component: Stub },
      { path: '/diagnostics', name: 'diagnostics', component: Stub },
    ],
  })
}

async function mountCard(device: Device, opts: { groups?: Group[]; maWebUrl?: string | null; devices?: Device[] } = {}) {
  const bridge = useBridgeStore()
  const status = makeStatus(opts.devices ?? [device])
  status.groups = opts.groups ?? []
  status.bridge.ma_web_url = opts.maWebUrl ?? null
  bridge.snapshot = status
  const router = makeRouter()
  await router.push('/')
  const wrapper = mount(DeviceCard, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] },
  })
  return { wrapper, router }
}

const streaming = (timing: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
  makeDevice({
    playback: { playing: true, track: { title: 'Song', artist: 'Band' } },
    audio: { streaming: true },
    timing: { available: true, ...timing },
    ...extra,
  })

const output = () =>
  makeDevice({
    backend: { type: 'pulse_sink', sink: 'alsa_output.usb', mac: null, adapter: null },
    bluetooth: null as never,
    health: { state: 'unknown' },
    audio: { sink_name: 'alsa_output.usb', has_sink: true, sink_missing: false },
  })

describe('DeviceCard chips', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  describe('artwork', () => {
    const withArt = () =>
      makeDevice({
        music_assistant: { now_playing: { track: 'Song', artist: 'Band', image_url: 'api/v1/ma/artwork?sig=1' } },
      })

    it('shows Music Assistant artwork beside the track and enlarges it on click', async () => {
      const { wrapper } = await mountCard(withArt())
      const img = wrapper.find('img')
      expect(img.attributes('src')).toContain('/api/v1/ma/artwork?sig=1')
      await wrapper.find('button[aria-label="Artwork Preview"]').trigger('click')
      expect(wrapper.findAll('img')).toHaveLength(2)
    })

    it('falls back to the music icon when the image fails', async () => {
      const { wrapper } = await mountCard(withArt())
      await wrapper.find('img').trigger('error')
      expect(wrapper.find('img').exists()).toBe(false)
    })
  })

  describe('sync badge', () => {
    it('says Sync with the sync error while streaming', async () => {
      const { wrapper } = await mountCard(streaming({ sync_error_ms: 3.2 }))
      expect(wrapper.find('[data-chip="sync"]').text()).toContain('Sync')
      expect(wrapper.find('[data-chip="sync"]').text()).toContain('3 ms')
      expect(wrapper.find('[data-chip="sync"] .tone-success').exists()).toBe(true)
    })

    it('says Resyncing, pulsing, while the stream re-anchors', async () => {
      const { wrapper } = await mountCard(streaming({ reanchoring: true }))
      const chip = wrapper.find('[data-chip="sync"]')
      expect(chip.text()).toContain('Resyncing')
      expect(chip.find('.tone-warning').exists()).toBe(true)
      expect(chip.find('.animate-pulse').exists()).toBe(true)
    })

    it('shows the re-anchor count with 5 and 30 minute counts in its tooltip', async () => {
      const { wrapper } = await mountCard(streaming({ reanchor_count: 4, reanchor_count_5m: 1, reanchor_count_30m: 3 }))
      const count = wrapper.find('[data-chip="reanchors"]')
      expect(count.text()).toContain('×4')
      expect(count.attributes('title')).toContain('1 in 5 min')
      expect(count.attributes('title')).toContain('3 in 30 min')
    })

    it('is hidden when not streaming or without timing', async () => {
      expect((await mountCard(makeDevice({ timing: { available: true } }))).wrapper.find('[data-chip="sync"]').exists()).toBe(false)
      expect((await mountCard(streaming({ available: false }))).wrapper.find('[data-chip="sync"]').exists()).toBe(false)
    })
  })

  describe('server chip', () => {
    const onServer = (server: Record<string, unknown>) => makeDevice({ playback: { server_connected: true, server } })

    it('names the Music Assistant the player is on', async () => {
      const { wrapper } = await mountCard(onServer({ name: 'Music Assistant HA', host: '192.168.10.10', own: true }))
      const chip = wrapper.find('[data-chip="server"]')
      expect(chip.text()).toContain('Music Assistant HA')
      expect(chip.find('.tone-warning').exists()).toBe(false)
    })

    it('warns when the server is not the bridge’s own', async () => {
      const { wrapper } = await mountCard(onServer({ name: 'Test MA', host: '192.168.10.229', own: false }))
      const chip = wrapper.find('[data-chip="server"]')
      expect(chip.find('.tone-warning').exists()).toBe(true)
      expect(chip.attributes('title')).toContain('192.168.10.229')
      expect(chip.attributes('title')).toContain('not this bridge')
    })

    it('is absent without a known server', async () => {
      const { wrapper } = await mountCard(makeDevice())
      expect(wrapper.find('[data-chip="server"]').exists()).toBe(false)
    })
  })

  describe('group chip', () => {
    const grouped = () =>
      makeDevice({
        music_assistant: { syncgroup_id: 'syncgroup_abcd1234' },
        playback: { group: { id: 'syncgroup_abcd1234', name: null } },
      })
    const group = { id: 'syncgroup_abcd1234', name: 'Downstairs', members: [], external_count: 2, external_members: [], playing: false, avg_volume: 50 } as Group

    it('names the group with members elsewhere and opens it in Music Assistant', async () => {
      const open = vi.spyOn(window, 'open').mockImplementation(() => null)
      const { wrapper } = await mountCard(grouped(), { groups: [group], maWebUrl: 'http://ma.local:8095' })
      const chip = wrapper.find('[data-chip="group"]')
      expect(chip.text()).toContain('Downstairs')
      expect(chip.text()).toContain('+2')
      await chip.trigger('click')
      expect(open).toHaveBeenCalledWith('http://ma.local:8095/#/settings/editplayer/syncgroup_abcd1234', '_blank', 'noopener,noreferrer')
    })

    it('shows # and the id tail for an unnamed group', async () => {
      const { wrapper } = await mountCard(grouped())
      expect(wrapper.find('[data-chip="group"]').text()).toContain('#1234')
    })
  })

  describe('adapter chip', () => {
    it('names the adapter and opens it in Configuration', async () => {
      const { wrapper, router } = await mountCard(makeDevice({ bluetooth: { adapter: { hci: 'hci1', name: 'Kitchen dongle' } } }))
      const chip = wrapper.find('[data-chip="adapter"]')
      expect(chip.text()).toContain('Kitchen dongle')
      expect(chip.find('.tone-success').exists()).toBe(true)
      await chip.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.name).toBe('config')
      expect(router.currentRoute.value.query.adapter).toBe('hci1')
      expect(router.currentRoute.value.hash).toBe('#settings-bluetooth')
    })

    it('is red while Bluetooth is disconnected', async () => {
      const { wrapper } = await mountCard(makeDevice({ bluetooth: { connected: false } }))
      expect(wrapper.find('[data-chip="adapter"] .tone-error').exists()).toBe(true)
    })

    it('shows the output (no link) for a local audio output', async () => {
      useAudioStore().sinks = [{ name: 'alsa_output.usb', description: 'USB DAC' } as never]
      const { wrapper } = await mountCard(output())
      expect(wrapper.find('[data-chip="adapter"]').exists()).toBe(false)
      const chip = wrapper.find('[data-chip="output"]')
      expect(chip.text()).toContain('USB DAC')
      expect(chip.element.tagName).not.toBe('BUTTON')
    })
  })

  describe('blocked-action hints', () => {
    const blocked = (recommended_action: string | null) =>
      makeDevice({
        capabilities: {
          actions: {
            play_pause: {
              supported: true,
              currently_available: false,
              blocked_reason: 'The Sendspin link is down.',
              depends_on: ['sendspin_connected'],
              recommended_action,
            },
          },
        },
      })

    it('says which control is unavailable and why', async () => {
      const { wrapper } = await mountCard(blocked(null))
      const hint = wrapper.find('[data-hint="play_pause"]')
      expect(hint.text()).toContain('Playback unavailable')
      expect(hint.get('[data-hint-why]').text()).toContain('Why?')
      expect(hint.text()).toContain('The Sendspin link is down.')
      expect(hint.text()).toContain('Needs: Sendspin link')
    })

    it('links to diagnostics when that is the recommended action', async () => {
      const { wrapper, router } = await mountCard(blocked('open_diagnostics'))
      await wrapper.get('[data-hint="play_pause"] [data-hint-diagnostics]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.name).toBe('diagnostics')
    })

    it('opens the explanation from a real button that reports its state', async () => {
      const { wrapper } = await mountCard(blocked(null))
      const why = wrapper.get('[data-hint="play_pause"] [data-hint-why]')
      expect(why.element.tagName).toBe('BUTTON')
      expect(why.attributes('type')).toBe('button')
      expect(why.attributes('aria-expanded')).toBe('false')
      const panel = wrapper.get(`#${why.attributes('aria-controls')}`)
      expect((panel.element as HTMLElement).style.display).toBe('none')
      await why.trigger('click')
      expect(why.attributes('aria-expanded')).toBe('true')
      expect((panel.element as HTMLElement).style.display).toBe('')
      expect(panel.text()).toContain('The Sendspin link is down.')
    })

    it('shows none when every control is available', async () => {
      expect((await mountCard(makeDevice())).wrapper.find('[data-hint]').exists()).toBe(false)
    })
  })

  describe('transfer readiness', () => {
    it('shows readiness coloured by severity, with the reason in the tooltip', async () => {
      const { wrapper } = await mountCard(
        makeDevice({ transfer_readiness: { ready: false, reason: 'bluetooth_disconnected', severity: 'error' } }),
      )
      const chip = wrapper.find('[data-chip="transfer"]')
      expect(chip.text()).toContain('Not ready')
      expect(chip.find('.tone-error').exists()).toBe(true)
      expect(chip.attributes('title')).toContain('Bluetooth disconnected')
    })

    it('is absent without a readiness payload', async () => {
      expect((await mountCard(makeDevice())).wrapper.find('[data-chip="transfer"]').exists()).toBe(false)
    })
  })

  it('shows no Bluetooth-only chips for a local audio output', async () => {
    const { wrapper } = await mountCard(output())
    expect(wrapper.find('[data-chip="adapter"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Δ dB')
  })
})
