import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import HomeView from '@/views/HomeView.vue'
import { useBridgeStore } from '@/stores/bridge'
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
  DisabledSpeakers: true,
  DeviceCard: { props: ['device'], template: '<div data-card>{{ device.name }}</div>' },
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
