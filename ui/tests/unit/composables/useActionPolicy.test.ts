import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { createApp } from 'vue'
import { confirmAction, undoable } from '@/composables/useActionPolicy'
import { confirmState, settleConfirm } from '@/composables/useConfirm'
import { useReversibleActions } from '@/composables/useReversibleActions'
import { useNotificationStore } from '@/stores/notifications'
import en from '@/i18n/en.json'

const store = vi.hoisted(() => ({ standby: vi.fn(), wake: vi.fn(), setPowerSave: vi.fn(), setEnabled: vi.fn(), release: vi.fn() }))
const playback = vi.hoisted(() => ({ setMute: vi.fn() }))
vi.mock('@/stores/devices', () => ({ useDeviceStore: () => store }))
vi.mock('@/api/playback', () => playback)

function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  let api!: ReturnType<typeof useReversibleActions>
  const app = createApp({ setup() { api = useReversibleActions(); return () => null } })
  app.use(pinia).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  app.mount(document.createElement('div'))
  return { api, notes: useNotificationStore() }
}

describe('confirmAction', () => {
  it('names the consequences, makes the button red for a destructive action and resolves with the answer', async () => {
    const answer = confirmAction('speaker.remove', { title: 'Remove Kitchen?', consequences: ['Playback stops on Kitchen'], confirmLabel: 'Remove' })
    expect(confirmState.options?.consequences).toEqual(['Playback stops on Kitchen'])
    expect(confirmState.options?.danger).toBe(true)
    settleConfirm(true)
    await expect(answer).resolves.toBe(true)
  })

  it('keeps a plain question neutral', async () => {
    const answer = confirmAction('sync.saveManualOnLeave', { title: 'Save?' })
    expect(confirmState.options?.danger).toBe(false)
    settleConfirm(false)
    await answer
  })

  it('refuses a reversible action: it must run at once with Undo', () => {
    expect(() => confirmAction('speaker.standby', { title: 'x' })).toThrow(/undoable/)
  })
})

describe('undoable', () => {
  it('refuses a disruptive action: it must ask first', async () => {
    await expect(undoable('speaker.remove', async () => 1, async () => 1, { doneKey: 'undo.disabled', name: 'x', failure: () => 'f' })).rejects.toThrow(/confirmAction/)
  })
})

describe('reversible speaker actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    for (const f of Object.values(store)) f.mockResolvedValue(undefined)
    playback.setMute.mockResolvedValue(undefined)
  })

  it('standby runs at once and Undo wakes the speaker', async () => {
    const { api, notes } = setup()
    await api.standby('d1', 'Kitchen')
    expect(store.standby).toHaveBeenCalledWith('d1')
    await notes.toasts[0]!.action!.run()
    expect(store.wake).toHaveBeenCalledWith('d1')
  })

  it('wake runs at once and Undo puts the speaker back on standby', async () => {
    const { api, notes } = setup()
    await api.wake('d1', 'Kitchen')
    await notes.toasts[0]!.action!.run()
    expect(store.standby).toHaveBeenCalledWith('d1')
  })

  it('enable runs at once and Undo disables again', async () => {
    const { api, notes } = setup()
    await api.enable('d1', 'Kitchen')
    expect(store.setEnabled).toHaveBeenCalledWith('d1', true)
    await notes.toasts[0]!.action!.run()
    expect(store.setEnabled).toHaveBeenLastCalledWith('d1', false)
  })

  it('mute all: one Undo unmutes exactly the speakers that were muted, and failures are named', async () => {
    playback.setMute.mockImplementation(async (id: string) => { if (id === 'b') throw new Error('x') })
    const { api, notes } = setup()
    const failed = vi.fn()
    await api.muteAll([{ id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta' }], failed)
    expect(failed).toHaveBeenCalledWith(['Beta'])
    expect(notes.toasts[0]!.message).toContain('Alpha')
    expect(notes.toasts[0]!.message).not.toContain('Beta')
    await notes.toasts[0]!.action!.run()
    expect(playback.setMute).toHaveBeenCalledWith('a', false)
    expect(playback.setMute).not.toHaveBeenCalledWith('b', false)
  })

  it('power save for several: Undo turns it off again', async () => {
    const { api, notes } = setup()
    await api.powerSaveAll([{ id: 'a', name: 'Alpha' }], vi.fn())
    expect(store.setPowerSave).toHaveBeenCalledWith('a', true)
    await notes.toasts[0]!.action!.run()
    expect(store.setPowerSave).toHaveBeenLastCalledWith('a', false)
  })
})
