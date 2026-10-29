import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import HomeView from '@/views/HomeView.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import en from '@/i18n/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/api/audio', () => ({ getAudioSinks: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/playback', () => ({ transport: vi.fn(), setVolume: vi.fn(), setMute: vi.fn(), setDevicePlayback: vi.fn() }))

const stubs = {
  DeviceDetailDrawer: true,
  AddSpeakerDialog: true,
  GuidanceBanner: true,
  DeviceCard: { props: ['device'], template: '<div data-card>{{ device.name }}</div>' },
  DisabledPlayerCard: { props: ['player'], template: '<div data-card data-disabled>{{ player.name }}</div>' },
  DisabledPlayerRow: { props: ['player'], template: '<tr data-disabled-row><td>{{ player.name }}</td></tr>' },
}

const idle = (id: string, name: string) => makeDevice({ id, name })
const streaming = (id: string, name: string) =>
  makeDevice({ id, name, playback: { playing: true, track: { title: 'Song' } }, audio: { streaming: true } })
const output = (id: string, name: string) =>
  makeDevice({
    id,
    name,
    backend: { type: 'pulse_sink', sink: 'alsa_output.x' },
    bluetooth: null,
    health: { state: 'unknown' },
    audio: { has_sink: true, sink_missing: false },
  } as never)

function mountHome(devices: Device[], view: 'grid' | 'list' = 'grid') {
  localStorage.setItem('sb-view-mode', view)
  const bridge = useBridgeStore()
  bridge.snapshot = makeStatus(devices)
  bridge.loading = false
  return mount(HomeView, {
    attachTo: document.body,
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs },
  })
}

/* Toolbar menus are SbDropdowns: open the trigger, pick the item (the menu sits on <body>). */
async function pick(w: ReturnType<typeof mountHome>, trigger: string, item: string) {
  await w.get(trigger).trigger('click')
  await nextTick()
  const el = document.body.querySelector<HTMLElement>(item)
  expect(el, item).not.toBeNull()
  el!.click()
  await nextTick()
}

const cardNames = (w: ReturnType<typeof mountHome>) => w.findAll('[data-card]').map((c) => c.text())

describe('HomeView toolbar', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('orders by activity first and switches order from the toolbar, remembering it', async () => {
    const w = mountHome([idle('a', 'Alpha'), streaming('z', 'Zulu')])
    expect(cardNames(w)).toEqual(['Zulu', 'Alpha'])
    const sort = w.get('button[data-sort]')
    expect(sort.text()).toContain('Activity')
    await pick(w, 'button[data-sort]', '[data-sort-option="name"]')
    expect(cardNames(w)).toEqual(['Alpha', 'Zulu'])
    expect(w.get('button[data-sort]').text()).toContain('Name')
    expect(localStorage.getItem('sb-device-sort')).toBe('name')
  })

  it('offers the backend filter only when both kinds of player exist', async () => {
    expect(mountHome([idle('a', 'Alpha'), idle('b', 'Bravo')]).find('[data-backend-filter]').exists()).toBe(false)
    setActivePinia(createPinia())
    const w = mountHome([idle('a', 'Alpha'), output('o', 'Output')])
    await pick(w, 'button[data-backend-filter]', '[data-backend-option="output"]')
    expect(cardNames(w)).toEqual(['Output'])
    expect(w.get('button[data-backend-filter]').text()).toContain('Audio outputs')
    await pick(w, 'button[data-backend-filter]', '[data-backend-option="bluetooth"]')
    expect(cardNames(w)).toEqual(['Alpha'])
  })

  it('drops a leftover backend filter when the setup is no longer mixed', () => {
    useDeviceStore().filter.backend = 'output'
    const w = mountHome([idle('a', 'Alpha'), idle('b', 'Bravo')])
    expect(useDeviceStore().filter.backend).toBe('')
    expect(cardNames(w)).toEqual(['Alpha', 'Bravo'])
  })
})

describe('HomeView summary', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('keeps the one-line summary for phones only (the header row has it from md up)', () => {
    const w = mountHome([idle('a', 'Alpha'), streaming('b', 'Bravo')])
    const line = w.get('[data-home-summary]')
    expect(line.classes()).toContain('md:hidden')
    expect(line.text()).toContain('1 playing')
  })
})

describe('HomeView list rows', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('opens the first streaming row on load and keeps one row open at a time', async () => {
    const w = mountHome([idle('a', 'Alpha'), streaming('b', 'Bravo'), idle('c', 'Charlie')], 'list')
    await flushPromises()
    const details = () => w.findAll('[data-row-detail]').map((d) => d.attributes('id'))
    expect(details()).toEqual(['device-row-detail-b'])
    const toggles = w.findAll('[data-row-toggle]')
    // activity order: Bravo, Alpha, Charlie
    await toggles[1]!.trigger('click')
    expect(details()).toEqual(['device-row-detail-a'])
    await w.findAll('[data-row-toggle]')[1]!.trigger('click')
    expect(details()).toEqual([])
  })
})

const offDevice = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  player_name: name,
  mac: null,
  backend_type: 'bluetooth_a2dp',
  adapter: null,
  sink: null,
  room_name: null,
  enabled: false,
  ...extra,
})

function mountWithDisabled(devices: Device[], disabled: ReturnType<typeof offDevice>[], view: 'grid' | 'list' = 'grid') {
  localStorage.setItem('sb-view-mode', view)
  const bridge = useBridgeStore()
  const status = makeStatus(devices)
  status.bridge.disabled_devices = disabled as never
  bridge.snapshot = status
  bridge.loading = false
  return mount(HomeView, {
    attachTo: document.body,
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs },
  })
}

describe('HomeView view toggle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('is there as soon as there is one player, saying which view is on', async () => {
    const w = mountHome([idle('a', 'Alpha')])
    const grid = w.get('button[aria-label="Grid view"]')
    const list = w.get('button[aria-label="List view"]')
    expect(grid.attributes('aria-pressed')).toBe('true')
    await list.trigger('click')
    expect(list.attributes('aria-pressed')).toBe('true')
    expect(localStorage.getItem('sb-view-mode')).toBe('list')
    expect(w.find('table').exists()).toBe(true)
  })

  it('is there with only disabled players too', () => {
    const w = mountWithDisabled([], [offDevice('x', 'Xray')])
    expect(w.find('button[aria-label="List view"]').exists()).toBe(true)
  })

  it('is absent without players', () => {
    expect(mountHome([]).find('button[aria-label="List view"]').exists()).toBe(false)
  })

  it('shows the grid on a phone, where the list does not fit, keeping the saved choice', () => {
    const original = window.matchMedia
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia
    try {
      const w = mountHome([idle('a', 'Alpha')], 'list')
      expect(w.find('table').exists()).toBe(false)
      expect(cardNames(w)).toEqual(['Alpha'])
      expect(localStorage.getItem('sb-view-mode')).toBe('list')
    } finally {
      window.matchMedia = original
    }
  })
})

describe('HomeView disabled players', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('shows them as cards among the others, last in activity order, with no separate section', () => {
    const w = mountWithDisabled([idle('a', 'Alpha'), streaming('b', 'Bravo')], [offDevice('x', 'Aaron')])
    expect(cardNames(w)).toEqual(['Bravo', 'Alpha', 'Aaron'])
    expect(w.findAll('[data-disabled]')).toHaveLength(1)
    expect(w.text()).not.toContain('Disabled speakers')
  })

  it('shows them as rows in the list', () => {
    const w = mountWithDisabled([idle('a', 'Alpha')], [offDevice('x', 'Xray')], 'list')
    expect(w.findAll('[data-disabled-row]').map((r) => r.text())).toEqual(['Xray'])
  })

  it('shows them instead of the empty state when every player is disabled', () => {
    const w = mountWithDisabled([], [offDevice('x', 'Xray')])
    expect(cardNames(w)).toEqual(['Xray'])
    expect(w.text()).not.toContain(en.devices.empty.title)
  })

  it('offers a Disabled status filter that picks them', async () => {
    const running = ['a', 'b', 'c', 'd', 'e'].map((id) => idle(id, id.toUpperCase()))
    const w = mountWithDisabled(running, [offDevice('x', 'Xray')])
    const chip = w.findAll('button').find((b) => b.text() === 'Disabled')
    expect(chip).toBeTruthy()
    await chip!.trigger('click')
    expect(cardNames(w)).toEqual(['Xray'])
  })

  it('keeps the header counts to running players', () => {
    const w = mountWithDisabled([idle('a', 'Alpha')], [offDevice('x', 'Xray')])
    expect(w.get('[data-home-summary]').text()).toContain('1 of 1 connected')
  })
})

describe('HomeView adapter filter', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  const onAdapter = (id: string, name: string, hci: string) => makeDevice({ id, name, bluetooth: { adapter: { hci } } } as never)
  // The filters appear from six players on.
  const filler = ['b', 'c', 'd', 'e'].map((id) => onAdapter(id, id.toUpperCase(), 'hci0'))
  const options = async (w: ReturnType<typeof mountHome>) => {
    await w.get('button[data-adapter-filter]').trigger('click')
    await nextTick()
    return [...document.body.querySelectorAll('[data-adapter-option]')].map((e) => e.textContent?.trim())
  }

  it('offers an adapter that only disabled speakers use', async () => {
    useBridgeStore().adapters = [{ id: 'hci1', mac: 'C0:FB:F9:62:D7:D6', name: 'Second', powered: true }] as never
    const w = mountWithDisabled(
      [onAdapter('a', 'Alpha', 'hci0'), ...filler],
      [offDevice('x', 'Xray', { adapter: 'C0:FB:F9:62:D7:D6' }), offDevice('y', 'Yankee', { adapter: 'hci1' })],
    )
    expect(await options(w)).toEqual(['hci0', 'hci1'])
  })

  it('offers no adapter for a disabled local output', async () => {
    const w = mountWithDisabled(
      [onAdapter('a', 'Alpha', 'hci0'), onAdapter('z', 'Zulu', 'hci1'), ...filler],
      [offDevice('o', 'Out', { backend_type: 'pulse_sink', adapter: 'hci9' })],
    )
    expect(await options(w)).toEqual(['hci0', 'hci1'])
  })
})

describe('HomeView empty filter results', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('offers Clear filters when nothing matches, and clears them', async () => {
    const w = mountHome([idle('a', 'Alpha'), idle('b', 'Bravo')])
    useDeviceStore().filter.search = 'zzz'
    await nextTick()
    expect(w.text()).toContain('No results')
    await w.get('[data-clear-filters]').trigger('click')
    expect(useDeviceStore().filter.search).toBe('')
    expect(cardNames(w)).toEqual(['Alpha', 'Bravo'])
  })

  it('clears the filters when the bar disappears (fewer than six players)', async () => {
    const many = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => idle(id, id.toUpperCase()))
    mountHome(many)
    useDeviceStore().filter.search = 'zzz'
    useDeviceStore().filter.status = ['idle']
    useBridgeStore().snapshot = makeStatus(many.slice(0, 2))
    await nextTick()
    await nextTick()
    expect(useDeviceStore().filter.search).toBe('')
    expect(useDeviceStore().filter.status).toEqual([])
  })
})
